#!/usr/bin/env python3
"""Tell Me!: menu-bar push-to-toggle voice dictation for talking to Claude Code.

Flow: hotkey starts mic recording -> hotkey again stops it -> local mlx-whisper
transcribes (HUD shows progress) -> result is prefixed with an instruction for
Claude, copied to the clipboard, and auto-pasted when a terminal is focused.

Everything tunable lives in config.json next to this file.
"""

import os
import sys
import json
import time
import threading
import logging
import ctypes
import ctypes.util
import fcntl

import numpy as np

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "logs", "app.log")

SAMPLE_RATE = 16000  # whisper expects 16 kHz mono float32

DEFAULTS = {
    "model": "mlx-community/whisper-large-v3-turbo",
    "hotkey": "<ctrl>+<alt>+<cmd>+<space>",
    "language": None,
    "whisper_initial_prompt": "",
    "paste_mode": "terminal",          # terminal | always | never
    "paste_delay_ms": 60,
    "type_chunk_delay_ms": 22,         # delay between typed chunks (clipboard:false); raise for slow/browser terminals
    "min_seconds": 0.3,
    "terminal_bundle_ids": [
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "com.mitchellh.ghostty",
        "com.github.wez.wezterm",
        "net.kovidgoyal.kitty",
        "io.alacritty",
        "dev.warp.Warp-Stable",
    ],
    "prompt_prefix": "",
}

# ---------------------------------------------------------------------------
# config + logging
# ---------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"config.json invalid, using defaults: {e}", file=sys.stderr)
    return cfg


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stderr)],
    )
    return logging.getLogger("whisper-dictation")


log = setup_logging()

# Heavy + UI imports after logging so failures are captured.
import rumps  # noqa: E402
import sounddevice as sd  # noqa: E402
from PyObjCTools import AppHelper  # noqa: E402
from Quartz import (  # noqa: E402
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    CGEventSetFlags,
    kCGHIDEventTap,
    kCGEventFlagMaskCommand,
)
from Foundation import NSTimer  # noqa: E402
from AppKit import (  # noqa: E402
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSPasteboard,
    NSPasteboardTypeString,
    NSWorkspace,
    NSWindow,
    NSScreen,
    NSProgressIndicator,
    NSTextField,
    NSColor,
    NSFont,
    NSImage,
    NSBezierPath,
    NSMakeSize,
    NSMakeRect,
    NSBackingStoreBuffered,
    NSWindowStyleMaskBorderless,
    NSVisualEffectView,
    NSVisualEffectMaterialPopover,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
)

# ---------------------------------------------------------------------------
# native global hotkey (Carbon RegisterEventHotKey)
#
# This registers ONE specific chord with the OS and only fires for it. Unlike a
# global keystroke listener it never sees other keys, and it needs no Input
# Monitoring / Accessibility permission.
# ---------------------------------------------------------------------------

_carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def _fourcc(s):
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


_K_KEYBOARD = _fourcc("keyb")
_K_HOTKEY_PRESSED = 5
_K_PARAM_DIRECT = _fourcc("----")  # kEventParamDirectObject is '----', not 'dobj'
_K_TYPE_HOTKEYID = _fourcc("hkid")
_EVENT_NOT_HANDLED = -9874  # eventNotHandledErr: let the event reach other handlers

# Carbon modifier masks for RegisterEventHotKey.
_CARBON_MODS = {
    "ctrl": 0x1000, "control": 0x1000,
    "alt": 0x0800, "option": 0x0800, "opt": 0x0800,
    "cmd": 0x0100, "command": 0x0100, "super": 0x0100,
    "shift": 0x0200,
}

# Virtual key codes (ANSI/US layout).
_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "esc": 53, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def parse_hotkey(spec):
    """'<ctrl>+<alt>+<cmd>+<space>' -> (carbon_modifier_mask, virtual_keycode)."""
    mods, keycode = 0, None
    for tok in spec.split("+"):
        tok = tok.strip().lower()
        if tok.startswith("<") and tok.endswith(">"):
            tok = tok[1:-1]
        if tok in _CARBON_MODS:
            mods |= _CARBON_MODS[tok]
        elif tok in _KEYCODES:
            keycode = _KEYCODES[tok]
        else:
            log.warning("hotkey: unknown token %r", tok)
    return mods, keycode


class CarbonHotKey:
    """Registers a single global hotkey; calls `callback` on the main thread."""

    def __init__(self, modifiers, keycode, callback, hotkey_id=1, strict=False):
        self._callback = callback
        self._id = hotkey_id
        self._strict = strict
        self._handler = _HANDLER(self._handle)  # keep ref alive for C side
        self._hotkey_ref = ctypes.c_void_p()
        self._handler_ref = ctypes.c_void_p()

        _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        target = _carbon.GetApplicationEventTarget()

        _carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HANDLER, ctypes.c_ulong,
            ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _carbon.InstallEventHandler.restype = ctypes.c_int32
        _carbon.GetEventParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(_EventHotKeyID),
        ]
        _carbon.GetEventParameter.restype = ctypes.c_int32
        spec = _EventTypeSpec(_K_KEYBOARD, _K_HOTKEY_PRESSED)
        self.install_status = _carbon.InstallEventHandler(
            target, self._handler, 1, ctypes.byref(spec), None, ctypes.byref(self._handler_ref)
        )

        _carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        _carbon.RegisterEventHotKey.restype = ctypes.c_int32
        hk_id = _EventHotKeyID(_fourcc("WDic"), hotkey_id)
        self.register_status = _carbon.RegisterEventHotKey(
            keycode, modifiers, hk_id, target, 0, ctypes.byref(self._hotkey_ref)
        )

    def _handle(self, next_handler, event, user_data):
        # Each instance installs its own handler that fires for every hotkey
        # event, so route by id and only act on our own hotkey.
        try:
            got = _EventHotKeyID()
            st = _carbon.GetEventParameter(
                event, _K_PARAM_DIRECT, _K_TYPE_HOTKEYID, None,
                ctypes.sizeof(got), None, ctypes.byref(got),
            )
            if st == 0:
                if got.id == self._id:
                    self._callback()
                    return 0  # handled
                return _EVENT_NOT_HANDLED  # not ours -> let the toggle handler see it
            if not self._strict:
                # Couldn't read the id: fire only for a lenient hotkey (the
                # toggle), never a strict one (Escape), so a read failure can
                # never trigger a spurious abort.
                self._callback()
                return 0
            return _EVENT_NOT_HANDLED
        except Exception:
            log.exception("hotkey callback failed")
            return _EVENT_NOT_HANDLED

    def unregister(self):
        # Remove BOTH the hotkey and the event handler, so a config reload that
        # re-registers does not leave a stale handler that double-fires.
        try:
            if self._hotkey_ref:
                _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
                _carbon.UnregisterEventHotKey(self._hotkey_ref)
                self._hotkey_ref = ctypes.c_void_p()
            if self._handler_ref:
                _carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
                _carbon.RemoveEventHandler(self._handler_ref)
                self._handler_ref = ctypes.c_void_p()
        except Exception:
            log.exception("hotkey unregister failed")


# ---------------------------------------------------------------------------
# transcription progress bridge (set by the app, called from mlx-whisper)
# ---------------------------------------------------------------------------

_progress_cb = None  # function(fraction: float) -> None


class _ProgBar:
    """Drop-in stand-in for tqdm.tqdm that forwards progress to the HUD."""

    def __init__(self, total=None, **kwargs):
        self.total = total or 0
        self.n = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, n=1):
        self.n += n
        if _progress_cb and self.total:
            try:
                _progress_cb(min(1.0, self.n / self.total))
            except Exception:
                pass

    def close(self):
        pass

    def set_description(self, *a, **k):
        pass

    def set_postfix(self, *a, **k):
        pass


class _FakeTqdmModule:
    tqdm = _ProgBar


def patch_progress():
    """Redirect mlx-whisper's tqdm to our HUD-forwarding bar (idempotent)."""
    import importlib

    mod = importlib.import_module("mlx_whisper.transcribe")
    if getattr(mod, "_wd_patched", False):
        return
    mod.tqdm = _FakeTqdmModule
    mod._wd_patched = True


def _rounded_mask_image(w, h, radius):
    """An exact-size rounded-rect mask so NSVisualEffectView clips to rounded
    corners. This is the documented way; a layer cornerRadius alone leaves
    square vibrancy corners (the white corners) on some macOS versions. The HUD
    is a fixed size, so the mask is rendered at that size (no stretching)."""
    img = NSImage.alloc().initWithSize_(NSMakeSize(w, h))
    img.lockFocus()
    NSColor.blackColor().set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, w, h), radius, radius
    ).fill()
    img.unlockFocus()
    return img


# ---------------------------------------------------------------------------
# main app
# ---------------------------------------------------------------------------

class DictationApp(rumps.App):
    IDLE_ICON = "🎤"
    REC_ICON = "🔴"
    BUSY_ICON = "⏳"

    def __init__(self, cfg):
        super().__init__("Tell Me!", title=self.IDLE_ICON, quit_button=None)
        # rumps relies on LSUIElement alone; on macOS 26 the status item only
        # renders if the activation policy is set explicitly. Accessory = menu
        # bar only, no Dock icon.
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.cfg = cfg
        self.recording = False
        self.busy = False
        self._stop_flag = threading.Event()
        self._gen = 0          # bumped on start/abort to invalidate stale workers
        self._esc_hk = None    # Escape hotkey, armed only while active
        self._hud = None
        self._target_app = None
        self._hud_timer = None
        self._hud_state = "idle"  # idle | recording | transcribing | done
        self._rtf = 0.45          # transcribe seconds per audio second (self-calibrates)
        self._rec_t0 = 0.0
        self._prog_t0 = 0.0
        self._prog_est = None     # estimated transcription seconds
        self._prog_real = 0.0     # true fraction from whisper (long clips)
        self._done_t0 = 0.0
        self._done_hold = 0.9

        self.status_item = rumps.MenuItem("Idle")
        self._toggle_item = rumps.MenuItem(
            f"Start / Stop  ({self._pretty_hotkey()})", callback=self._menu_toggle
        )
        self._model_item = rumps.MenuItem(f"Model: {cfg['model'].split('/')[-1]}")
        self.menu = [
            self.status_item,
            None,
            self._toggle_item,
            None,
            self._model_item,
            rumps.MenuItem("Open config", callback=self._open_config),
            rumps.MenuItem("Reload config", callback=self._reload_config),
            rumps.MenuItem("Open logs", callback=self._open_logs),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        global _progress_cb
        _progress_cb = lambda frac: AppHelper.callAfter(self._set_progress, frac)

        if not os.environ.get("WD_NO_HOTKEY"):
            self._start_hotkeys()
        # rumps sets the menu-bar glyph via the deprecated NSStatusItem.setTitle_,
        # which renders nothing on modern macOS. Once the run loop has created the
        # status item, set the glyph on its button instead.
        AppHelper.callAfter(self._set_icon, self.IDLE_ICON)
        log.info("started; hotkey=%s model=%s", cfg["hotkey"], cfg["model"])

    # -- hotkeys -----------------------------------------------------------
    def _pretty_hotkey(self):
        return (
            self.cfg["hotkey"]
            .replace("<ctrl>", "⌃").replace("<alt>", "⌥").replace("<cmd>", "⌘")
            .replace("<shift>", "⇧").replace("<space>", "Space").replace("+", "")
        )

    def _start_hotkeys(self):
        mods, keycode = parse_hotkey(self.cfg["hotkey"])
        if keycode is None:
            log.error("no key in hotkey %r; not registering", self.cfg["hotkey"])
            return
        self._hk = CarbonHotKey(mods, keycode, self._on_hotkey)
        if self._hk.register_status != 0 or self._hk.install_status != 0:
            log.error("hotkey register failed (install=%s register=%s); is the chord taken?",
                      self._hk.install_status, self._hk.register_status)
        else:
            log.info("hotkey registered: %s", self._pretty_hotkey())

    def _on_hotkey(self):
        # Carbon delivers this on the main thread; defer one tick to be safe.
        AppHelper.callAfter(self.toggle)

    def _menu_toggle(self, _):
        self.toggle()

    # -- Escape to abort (armed only while recording / transcribing) -------
    def _arm_escape(self):
        if os.environ.get("WD_NO_HOTKEY"):
            return
        self._disarm_escape()
        try:
            hk = CarbonHotKey(0, 53, self._on_escape, hotkey_id=2, strict=True)  # 53 = Escape
            if hk.register_status != 0:
                log.warning("Escape hotkey register failed (status=%s)", hk.register_status)
                hk.unregister()
                return
            self._esc_hk = hk
        except Exception:
            log.exception("could not arm Escape")

    def _disarm_escape(self):
        if self._esc_hk is not None:
            try:
                self._esc_hk.unregister()
            except Exception:
                log.exception("could not disarm Escape")
            self._esc_hk = None

    def _on_escape(self):
        AppHelper.callAfter(self._abort_current)

    def _abort_current(self):
        if not self.recording and not self.busy:
            return
        self._gen += 1          # invalidate the in-flight worker (drops its result)
        self._stop_flag.set()   # break the recording read loop, if recording
        self.recording = False
        self.busy = False
        self._set_icon(self.IDLE_ICON)
        self.status_item.title = "Idle"
        self._disarm_escape()
        self._hud_message("Aborted", 0.8)
        log.info("aborted by Escape")

    # -- record / transcribe ----------------------------------------------
    def toggle(self):
        if self.busy:
            self._notify("Still transcribing, one moment…")
            return
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        self._stop_flag = threading.Event()
        self._gen += 1
        gen = self._gen
        self.recording = True
        self._set_icon(self.REC_ICON)
        self.status_item.title = f"Recording…  ({self._pretty_hotkey()} to stop)"
        self._rec_t0 = time.monotonic()
        self._show_hud("recording")
        self._arm_escape()  # Escape aborts while recording/transcribing
        # Record on a worker thread using blocking reads (no realtime Python
        # callback). Stopping the stream then happens on this worker thread with
        # no callback in flight, so it never contends for the GIL on the main
        # thread -> no CoreAudio AudioOutputUnitStop deadlock.
        threading.Thread(
            target=self._record_and_transcribe, args=(self._stop_flag, gen), daemon=True
        ).start()

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self.busy = True
        self._set_icon(self.BUSY_ICON)
        self.status_item.title = "Transcribing…"
        self._target_app = self._frontmost_bundle_id()
        self._prog_est = None  # until _begin_progress measures it
        self._prog_real = 0.0
        self._show_hud("transcribing")
        self._stop_flag.set()  # worker finishes the read loop, then transcribes

    def _record_and_transcribe(self, stop_flag, gen):
        block = max(160, int(SAMPLE_RATE * 0.1))  # ~0.1s blocks -> snappy stop
        frames = []

        # Open the stream with a few retries: PortAudio can return a transient
        # -9986 right after launch or if the device was just released.
        stream = None
        last_err = None
        for attempt in range(3):
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block
                )
                stream.start()
                break
            except Exception as e:
                last_err = e
                log.warning("mic open attempt %d/3 failed: %s", attempt + 1, e)
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
                stream = None
                if stop_flag.is_set():
                    break
                time.sleep(0.3)
        if stream is None:
            AppHelper.callAfter(self._finish_error, f"mic error: {last_err}")
            return

        # Blocking reads on this worker thread (no realtime Python callback), so
        # stop/close never contends for the GIL on the main thread.
        try:
            while not stop_flag.is_set():
                data, _overflow = stream.read(block)
                frames.append(data.copy())
        except Exception as e:
            log.exception("recording read error")
            AppHelper.callAfter(self._finish_error, f"mic error: {e}")
            return
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("error closing stream")

        if gen != self._gen:  # Escape aborted, or a new recording superseded this one
            log.info("recording aborted; discarding")
            return
        if not frames:
            AppHelper.callAfter(self._finish_short)
            return
        audio = np.concatenate(frames, axis=0).reshape(-1).astype(np.float32)
        dur = len(audio) / SAMPLE_RATE
        if dur < float(self.cfg.get("min_seconds", 0.3)):
            AppHelper.callAfter(self._finish_short)
            return

        try:
            import mlx_whisper

            patch_progress()
            kwargs = dict(path_or_hf_repo=self.cfg["model"], verbose=False)
            if self.cfg.get("language"):
                kwargs["language"] = self.cfg["language"]
            if self.cfg.get("whisper_initial_prompt"):
                kwargs["initial_prompt"] = self.cfg["whisper_initial_prompt"]
            AppHelper.callAfter(self._begin_progress, dur)
            t0 = time.time()
            result = mlx_whisper.transcribe(audio, **kwargs)
            elapsed = time.time() - t0
            text = (result.get("text") or "").strip()
            log.info("transcribed %.1fs audio in %.1fs -> %d chars", dur, elapsed, len(text))
        except Exception as e:
            log.exception("transcription failed")
            AppHelper.callAfter(self._finish_error, str(e))
            return
        if gen != self._gen:  # aborted during transcription -> drop the result
            log.info("transcription aborted; discarding result")
            return
        AppHelper.callAfter(self._finish_success, text, dur, elapsed)

    # -- completion (main thread) -----------------------------------------
    def _finish_short(self):
        self.busy = False
        self.recording = False
        self._disarm_escape()
        self._set_icon(self.IDLE_ICON)
        self.status_item.title = "Idle"
        self._hud_message("Too short, ignored", 1.2)

    def _finish_error(self, msg):
        self.busy = False
        self.recording = False
        self._disarm_escape()
        self._set_icon(self.IDLE_ICON)
        self.status_item.title = "Idle"
        log.error("finish error: %s", msg)
        self._hud_message("Error (see logs)", 1.8)

    def _finish_success(self, text, dur=0.0, elapsed=0.0):
        self.busy = False
        self.recording = False
        self._disarm_escape()
        self._set_icon(self.IDLE_ICON)
        self.status_item.title = "Idle"
        if dur > 0.2 and elapsed > 0:  # self-calibrate the ETA estimate
            self._rtf = max(0.05, min(3.0, 0.7 * self._rtf + 0.3 * (elapsed / dur)))
        if not text:
            self._hud_message("No speech detected", 1.2)
            return
        full = self.cfg.get("prompt_prefix", "") + text
        use_clipboard = self.cfg.get("clipboard", True)
        deliver = self._should_deliver()
        if use_clipboard:
            self._set_clipboard(full)
            if deliver and self._do_paste(self._send_cmd_v):
                msg = "Pasted ✓"
            else:
                msg = "Copied to clipboard ✓"
        elif deliver and self._do_paste(lambda: self._type_text(full)):
            msg = "Typed ✓"  # clipboard left untouched
        else:
            # Clipboard disabled but nowhere to type -> copy as a fallback so the
            # transcript is not lost.
            self._set_clipboard(full)
            msg = "Copied (no paste target)"
        self._hud_done(msg)
        log.info("delivered: %s", msg)

    # -- clipboard + paste -------------------------------------------------
    def _set_clipboard(self, text):
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

    def _frontmost_bundle_id(self):
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.bundleIdentifier() if app else None

    def _should_deliver(self):
        """Whether to paste/type into the focused app, per paste_mode."""
        mode = self.cfg.get("paste_mode", "terminal")
        if mode == "never":
            return False
        if mode == "always":
            return True
        return self._frontmost_bundle_id() in set(self.cfg.get("terminal_bundle_ids", []))

    def _do_paste(self, action):
        try:
            time.sleep(self.cfg.get("paste_delay_ms", 60) / 1000.0)
            action()
            return True
        except Exception:
            log.exception("paste/type failed (grant Accessibility?)")
            return False

    def _send_cmd_v(self):
        """Synthesize Cmd-V via Quartz (needs Accessibility)."""
        v_keycode = 9  # ANSI 'v'
        down = CGEventCreateKeyboardEvent(None, v_keycode, True)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        up = CGEventCreateKeyboardEvent(None, v_keycode, False)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)

    def _type_text(self, text):
        """Type text into the focused app via synthesized Unicode key events,
        leaving the clipboard untouched (needs Accessibility).

        Two things matter for browser-based terminals such as the Claude Agents
        xterm.js app (native apps like Terminal/VS Code tolerate the old, sloppier
        version):

        1. The Unicode string is attached only to the key-DOWN event. When both
           down and up carry the string, the browser inserts the chunk twice,
           which produced the scrambled, overlapping paste.
        2. The per-chunk delay must be large enough for the terminal to drain its
           hidden input textarea before the next chunk arrives. At 4 ms the chunks
           raced and the textarea was re-read mid-fill, so fragments restarted at
           20-char boundaries. ``type_chunk_delay_ms`` (default 22) is tunable.
        """
        delay = max(0, self.cfg.get("type_chunk_delay_ms", 22)) / 1000.0
        for i in range(0, len(text), 20):  # ~20 UTF-16 units is the reliable max per event
            piece = text[i:i + 20]
            down = CGEventCreateKeyboardEvent(None, 0, True)
            CGEventKeyboardSetUnicodeString(down, len(piece), piece)
            CGEventPost(kCGHIDEventTap, down)
            up = CGEventCreateKeyboardEvent(None, 0, False)  # key-up carries no string
            CGEventPost(kCGHIDEventTap, up)
            time.sleep(delay)

    # -- HUD (main thread) -------------------------------------------------
    # A borderless frosted panel with two labels (semantic colors so it reads
    # in light and dark themes) and a progress bar. One repeating timer drives
    # it through states: recording (elapsed clock) -> transcribing (% + ETA) ->
    # done (✓, auto-hides).
    def _build_hud(self):
        w, h = 320, 104
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(True)
        win.setLevel_(3)  # floating, above normal windows
        win.setReleasedWhenClosed_(False)
        win.setIgnoresMouseEvents_(True)  # informational, click-through
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        ve = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        ve.setMaterial_(NSVisualEffectMaterialPopover)
        ve.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        ve.setState_(NSVisualEffectStateActive)
        ve.setMaskImage_(_rounded_mask_image(w, h, 16))  # clip vibrancy -> no white corners
        win.setContentView_(ve)

        primary = NSTextField.alloc().initWithFrame_(NSMakeRect(22, 58, w - 44, 22))
        secondary = NSTextField.alloc().initWithFrame_(NSMakeRect(22, 14, w - 44, 16))
        for f in (primary, secondary):
            f.setBezeled_(False)
            f.setDrawsBackground_(False)
            f.setEditable_(False)
            f.setSelectable_(False)
        primary.setTextColor_(NSColor.labelColor())
        primary.setFont_(NSFont.systemFontOfSize_(15))
        secondary.setTextColor_(NSColor.secondaryLabelColor())
        secondary.setFont_(NSFont.systemFontOfSize_(11))
        ve.addSubview_(primary)
        ve.addSubview_(secondary)

        prog = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(22, 36, w - 44, 16))
        prog.setStyle_(0)  # bar
        prog.setIndeterminate_(True)
        prog.setMinValue_(0.0)
        prog.setMaxValue_(1.0)
        ve.addSubview_(prog)

        win.invalidateShadow()  # recompute the drop shadow to the rounded shape
        self._hud = {"win": win, "primary": primary, "secondary": secondary, "prog": prog}

    def _position_hud(self):
        win = self._hud["win"]
        scr = NSScreen.mainScreen()
        if scr is None:
            win.center()
            return
        vf = scr.visibleFrame()
        wsize = win.frame().size
        x = vf.origin.x + (vf.size.width - wsize.width) / 2.0
        y = vf.origin.y + 120  # near the bottom, out of the way
        win.setFrameOrigin_((x, y))

    def _show_hud(self, state):
        if self._hud is None:
            self._build_hud()
        self._hud_state = state
        p = self._hud["prog"]
        p.setIndeterminate_(True)
        p.startAnimation_(None)
        if state == "recording":
            self._hud["primary"].setStringValue_("Recording…")
            self._hud["secondary"].setStringValue_("0:00")
        else:
            self._hud["primary"].setStringValue_("Transcribing…")
            self._hud["secondary"].setStringValue_("")
        self._position_hud()
        self._hud["win"].orderFrontRegardless()
        self._ensure_timer()

    def _begin_progress(self, dur):
        """Switch the visible HUD from indeterminate to a determinate ETA bar."""
        if self._hud is None or self._hud_state not in ("transcribing", "recording"):
            return
        self._hud_state = "transcribing"
        self._prog_t0 = time.monotonic()
        self._prog_est = max(0.4, dur * self._rtf)
        self._prog_real = 0.0
        p = self._hud["prog"]
        p.stopAnimation_(None)
        p.setIndeterminate_(False)
        p.setDoubleValue_(0.0)

    def _set_progress(self, frac):
        # Called from mlx-whisper's progress hook; true per-window fraction
        # (only granular on clips > 30s). The timer reads this each tick.
        self._prog_real = float(max(0.0, min(1.0, frac)))

    def _tick(self):
        if self._hud is None:
            return
        st = self._hud_state
        if st == "recording":
            self._hud["secondary"].setStringValue_(self._fmt_clock(time.monotonic() - self._rec_t0))
        elif st == "transcribing":
            if self._prog_est is None:
                return
            elapsed = time.monotonic() - self._prog_t0
            time_frac = elapsed / self._prog_est if self._prog_est > 0 else 1.0
            frac = min(0.97, max(self._prog_real, time_frac))
            if self._prog_real > 0.05:
                eta = elapsed * (1.0 - self._prog_real) / self._prog_real
            else:
                eta = max(0.0, self._prog_est - elapsed)
            self._hud["prog"].setDoubleValue_(frac)
            self._hud["primary"].setStringValue_(f"Transcribing…  {int(frac * 100)}%")
            self._hud["secondary"].setStringValue_(self._fmt_eta(eta))
        elif st == "done":
            if time.monotonic() - self._done_t0 >= self._done_hold:
                self._hide_hud()

    @staticmethod
    def _fmt_clock(secs):
        s = int(secs)
        return f"{s // 60}:{s % 60:02d}"

    @staticmethod
    def _fmt_eta(eta):
        if eta < 1.0:
            return "almost done…"
        if eta < 60:
            return f"about {int(round(eta))}s left"
        m, s = divmod(int(round(eta)), 60)
        return f"about {m}m {s:02d}s left"

    def _hud_done(self, text):
        if self._hud is None:
            self._notify(text)
            return
        p = self._hud["prog"]
        p.stopAnimation_(None)
        p.setIndeterminate_(False)
        p.setDoubleValue_(1.0)
        self._hud["primary"].setStringValue_(text)
        self._hud["secondary"].setStringValue_("")
        self._hud_state = "done"
        self._done_hold = 0.9
        self._done_t0 = time.monotonic()
        self._ensure_timer()

    def _hud_message(self, text, hold):
        """Show a transient message (too short / error / no speech), then hide."""
        if self._hud is None:
            self._notify(text)
            return
        p = self._hud["prog"]
        p.stopAnimation_(None)
        p.setIndeterminate_(False)
        p.setDoubleValue_(0.0)
        self._hud["primary"].setStringValue_(text)
        self._hud["secondary"].setStringValue_("")
        self._hud_state = "done"
        self._done_hold = hold
        self._done_t0 = time.monotonic()
        self._ensure_timer()

    def _ensure_timer(self):
        if self._hud_timer is None:
            self._hud_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                0.2, True, lambda t: self._tick()
            )

    def _stop_timer(self):
        if self._hud_timer is not None:
            self._hud_timer.invalidate()
            self._hud_timer = None

    def _hide_hud(self):
        self._stop_timer()
        self._hud_state = "idle"
        if self._hud is not None:
            self._hud["prog"].stopAnimation_(None)
            self._hud["win"].orderOut_(None)

    # -- misc menu ---------------------------------------------------------
    # State glyph -> (SF Symbol name, tint). SF Symbol template images render
    # reliably in the menu bar; an emoji title does not on macOS 26.
    _SYMBOLS = {IDLE_ICON: "mic", REC_ICON: "mic.fill", BUSY_ICON: "hourglass"}

    def _set_icon(self, glyph):
        """Set the menu-bar glyph on the status item's button, preferring an SF
        Symbol image (reliable) and falling back to the emoji title."""
        self.title = glyph  # keep rumps' internal state in sync
        try:
            item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
            if item is None:
                return
            button = item.button()
            if button is not None:
                symbol = self._SYMBOLS.get(glyph)
                img = (
                    NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "Tell Me!")
                    if symbol else None
                )
                if img is not None:
                    img.setTemplate_(True)
                    img.setSize_(NSMakeSize(18, 18))  # ensure the item has non-zero width
                    button.setImage_(img)
                    button.setTitle_("")
                    button.setContentTintColor_(
                        NSColor.systemRedColor() if glyph == self.REC_ICON else None
                    )
                else:  # SF Symbol unavailable -> emoji fallback
                    button.setImage_(None)
                    button.setTitle_(glyph)
            if hasattr(item, "setVisible_"):
                item.setVisible_(True)
        except Exception:
            log.exception("could not set status item button")

    def _notify(self, msg):
        try:
            rumps.notification("Tell Me!", "", msg)
        except Exception:
            log.info("notify: %s", msg)

    def _open_config(self, _):
        os.system(f'open "{CONFIG_PATH}"')

    def _reload_config(self, _):
        """Re-read config.json and apply it live (no restart needed)."""
        self.cfg = load_config()
        # Re-register the (possibly changed) hotkey.
        try:
            if getattr(self, "_hk", None):
                self._hk.unregister()
        except Exception:
            log.exception("error releasing old hotkey on reload")
        if not os.environ.get("WD_NO_HOTKEY"):
            self._start_hotkeys()
        # Refresh menu labels; model/paste/prompt take effect on the next clip.
        self._toggle_item.title = f"Start / Stop  ({self._pretty_hotkey()})"
        self._model_item.title = f"Model: {self.cfg['model'].split('/')[-1]}"
        self._notify("Config reloaded")
        log.info("config reloaded; hotkey=%s model=%s", self.cfg["hotkey"], self.cfg["model"])

    def _open_logs(self, _):
        os.system(f'open "{LOG_PATH}"')

    def _quit(self, _):
        self._stop_flag.set()  # let any recorder thread close its stream
        try:
            self._hk.unregister()
        except Exception:
            pass
        rumps.quit_application()


_lock_fd = None  # held for process lifetime to enforce a single instance


def _acquire_single_instance_lock():
    """Return True if we got the lock; False if another instance holds it.

    flock is released automatically when the process dies, so there is never a
    stale lock. Lets the login LaunchAgent and a Spotlight/Finder launch coexist
    without ever running two copies.
    """
    global _lock_fd
    _lock_fd = open(os.path.join(APP_DIR, ".tellme.lock"), "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    if not _acquire_single_instance_lock():
        log.info("another Tell Me! instance is already running; exiting")
        return
    cfg = load_config()
    DictationApp(cfg).run()


if __name__ == "__main__":
    main()
