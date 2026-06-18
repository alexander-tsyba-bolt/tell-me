#!/usr/bin/env python3
"""Smoke test the PyObjC/AppKit wiring without a full GUI run or permission prompts.

Validates that every Cocoa selector used by the HUD and clipboard is correct.
Run with: WD_NO_HOTKEY=1 .venv/bin/python selftest.py
"""

import os

os.environ["WD_NO_HOTKEY"] = "1"

from AppKit import NSApplication  # noqa: E402

import app  # noqa: E402

NSApplication.sharedApplication()

cfg = app.load_config()
a = app.DictationApp(cfg)

# Build + drive the HUD state machine in memory (no orderFront, nothing flashes).
a._build_hud()
a._hud_state = "recording"
a._tick()                  # recording clock branch
a._begin_progress(5.0)     # -> transcribing, determinate bar
a._set_progress(0.5)
a._tick()                  # percent + ETA branch
a._hud_done("Pasted ✓")
a._tick()                  # done branch
assert a._fmt_clock(65) == "1:05", a._fmt_clock(65)
assert "left" in a._fmt_eta(3.0), a._fmt_eta(3.0)
a._hide_hud()

# Clipboard round-trip.
a._set_clipboard("whisper-dictation selftest ✓")
from AppKit import NSPasteboard, NSPasteboardTypeString  # noqa: E402

got = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
assert got == "whisper-dictation selftest ✓", repr(got)

# Frontmost-app probe + terminal detection logic.
bid = a._frontmost_bundle_id()

# Native Carbon hotkey registration round-trip (0 == noErr).
mods, keycode = app.parse_hotkey(cfg["hotkey"])
assert keycode is not None, "hotkey has no key"
hk = app.CarbonHotKey(mods, keycode, lambda: None)
assert hk.install_status == 0, ("InstallEventHandler", hk.install_status)
assert hk.register_status == 0, ("RegisterEventHotKey", hk.register_status)
hk.unregister()

# Quartz Cmd-V event can be constructed (not posted, to avoid a stray paste).
from Quartz import CGEventCreateKeyboardEvent  # noqa: E402

assert CGEventCreateKeyboardEvent(None, 9, True) is not None

# Live config reload (WD_NO_HOTKEY skips the real hotkey re-registration).
a._reload_config(None)
assert a._model_item.title.startswith("Model:"), a._model_item.title

# Single-instance lock acquires when free.
assert app._acquire_single_instance_lock() is True

print(
    "OK: imports, HUD selectors, progress, clipboard, Carbon hotkey reg, "
    "Quartz paste, reload, single-instance lock; frontmost=%r" % bid
)
