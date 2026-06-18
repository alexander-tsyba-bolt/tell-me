# Tell Me!

Local, hotkey-driven voice dictation for your Mac menu bar, built for talking to
Claude Code or any AI assistant. Press a hotkey to record your mic, press it again
to stop. A local `mlx-whisper` model transcribes on-device, the result is delivered
to the focused app, and nothing ever leaves your machine.

---

## Quick start for AI assistants / LLMs

> **If you are an AI assistant helping a colleague set up Tell Me!, read this
> section first.** Ask the user the four questions below before touching
> `config.json`: the answers determine which settings to change. Everything else
> can stay at its default.

**Ask your user:**

1. **Where do you want the transcript to land?**
   - *"Paste into any focused app automatically"* → `"paste_mode": "always"`
   - *"Only paste into terminals / VS Code / Cursor"* (default) → `"paste_mode": "terminal"`
   - *"Just put it on the clipboard, I'll paste myself"* → `"paste_mode": "never"`

2. **Should transcription overwrite your clipboard?**
   - *"Yes, copy it to clipboard as well"* (default) → `"clipboard": true`
   - *"No, type it in directly without touching my clipboard"* → `"clipboard": false`
     Note: `clipboard: false` requires `paste_mode` to be `"always"` or `"terminal"`
     (otherwise there is nowhere to type and it falls back to copying anyway).

3. **Which language(s) do you speak?**
   - Mostly one language → set `"language"` to its code (`"en"`, `"de"`, `"ru"`, …).
     Auto-detect can guess wrong on short clips or mixed-language phrases.
   - Mixed / unpredictable → leave `"language": null` (auto-detect).

4. **What names, acronyms, or product terms do you say often?**
   - List them in `"whisper_initial_prompt"` as a comma-separated string, e.g.:
     `"Bolt, Topsort, GMV, Jens, Fabrizio"`. Whisper uses this to bias spelling of
     words it would otherwise mis-hear. Keep it short (10–20 terms max).

After editing `config.json`, tell the user to pick **Reload config** from the
menu-bar icon to apply changes instantly.

---

## How it works

```
⌃⌥⌘Space ──▶ mic records  ──▶  ⌃⌥⌘Space  ──▶  mlx-whisper transcribes
             (popup shows              (popup shows % + ETA)
              recording clock)                   │
                                    prefix + transcript
                                                 │
                          paste_mode allows it? ─┤
                                                 │
                    clipboard:true  ──▶  copy + Cmd-V into focused app
                    clipboard:false ──▶  type directly, clipboard untouched
```

**Press Esc** at any point (during recording or transcription) to abort. Nothing
is transcribed or pasted; the popup disappears immediately.

The menu-bar icon shows state:

| Icon | State |
|------|-------|
| mic (outline) | idle |
| mic (filled, red) | recording |
| hourglass | transcribing |

A small frosted popup tracks each phase: a live `M:SS` recording clock, then
`Transcribing… 45%  about 3s left`, then a `Pasted ✓` / `Typed ✓` /
`Copied to clipboard ✓` confirmation that auto-hides.

---

## Requirements

- **Apple Silicon Mac** (MLX runs on the Neural Engine / GPU).
- [Homebrew](https://brew.sh) (the installer uses it for `portaudio`).
- [`uv`](https://docs.astral.sh/uv/) recommended, or `python@3.12`
  (`brew install python@3.12`). Python 3.14 is avoided because some wheels (MLX,
  PyObjC) lag behind it.

The Whisper model (~1.6 GB) is **not** in this repo. It downloads once from
Hugging Face on the first transcription and caches in `~/.cache/huggingface`.

---

## Install

```bash
git clone https://github.com/alexander-tsyba-bolt/tell-me.git
cd tell-me
./install.sh
```

This installs `portaudio`, creates `.venv`, installs Python dependencies, builds
`Tell Me!.app` into `/Applications` (or `~/Applications` if that is not writable),
and registers a LaunchAgent that starts the app at login and relaunches it on crash
(but not on a clean Quit from the menu). A microphone icon appears in the menu bar.

Keep the cloned folder where it is. The app bundle and the LaunchAgent both point
at it.

### Grant permissions on first use

System Settings ▸ Privacy & Security. Add **Tell Me!** (or the generic **Python**
entry if that is what macOS shows):

| Permission | Why | Required? |
|------------|-----|-----------|
| **Microphone** | record your voice | always |
| **Accessibility** | send Cmd-V or synthesize keystrokes | only when `paste_mode` is not `"never"` |

The global hotkey uses the native Carbon `RegisterEventHotKey` API: it needs **no**
Input Monitoring permission and only ever sees the one chord you registered.

---

## Usage

| Action | How |
|--------|-----|
| Start recording | press **⌃⌥⌘Space** (default, configurable) |
| Stop + transcribe | press **⌃⌥⌘Space** again |
| Abort silently | press **Esc**: nothing is pasted or copied |
| Launch | auto-starts at login; also launchable from Spotlight / Finder / Dock |
| Reload config | menu ▸ **Reload config**: re-applies `config.json` and re-registers the hotkey, no restart needed |
| View logs | menu ▸ **Open logs** |
| Quit | menu ▸ **Quit** (the LaunchAgent does NOT restart on a clean quit) |

Every transcript is prefixed with a short note telling the AI it was voice-
transcribed and that names / acronyms / non-English words may need correcting.
This is configurable via `prompt_prefix`.

---

## Configuration: `config.json`

`install.sh` creates `config.json` from `config.example.json`. Edit via the menu
(**Open config**), then pick **Reload config**. For a full restart:
`launchctl kickstart -k gui/$(id -u)/com.atsyba.tellme`.

### Delivery settings (start here)

| Key | Default | Options / notes |
|-----|---------|-----------------|
| `paste_mode` | `"terminal"` | `"always"`: deliver to any focused app. `"terminal"`: only to terminals + VS Code/Cursor (see `terminal_bundle_ids`). `"never"`: put on clipboard only, paste yourself. |
| `clipboard` | `true` | `true`: copy to clipboard then Cmd-V paste. `false`: type directly into the focused app via synthesized keystrokes, clipboard left untouched. Fallback: if no paste target is available, copies regardless. |
| `paste_delay_ms` | `60` | Milliseconds to wait before pasting/typing, to let the focused app settle |
| `terminal_bundle_ids` | see example | macOS bundle IDs of apps that count as "terminal" for `paste_mode: terminal`. Add any app's ID here. |

### Transcription settings

| Key | Default | Options / notes |
|-----|---------|-----------------|
| `model` | `"mlx-community/whisper-large-v3-turbo"` | Any `mlx-community` Whisper repo on Hugging Face. Smaller models (`whisper-small`, `distil-large-v3`) are faster but less accurate. |
| `language` | `null` | `null` = auto-detect per clip. Set to a BCP-47 code (`"en"`, `"de"`, `"ru"`, `"fr"`, …) to force a language and avoid mis-detection on short clips. |
| `whisper_initial_prompt` | `""` | Short comma-separated list of names, acronyms, and domain terms you say often. Whisper uses this to bias spelling. Keep it under ~20 terms; longer prompts can cause hallucination. |
| `min_seconds` | `0.3` | Clips shorter than this are silently discarded. |

### UI and prefix settings

| Key | Default | Notes |
|-----|---------|-------|
| `hotkey` | `"<ctrl>+<alt>+<cmd>+<space>"` | Modifier tokens: `<ctrl>` `<alt>` `<cmd>` `<shift>`. Key tokens: any letter, `<space>`, `<escape>`, `<return>`, `<f1>`–`<f12>`. |
| `prompt_prefix` | the Claude instruction | Text prepended to every transcript before delivery. Set to `""` to disable. |

### Example: no clipboard, paste everywhere, German

```json
{
  "paste_mode": "always",
  "clipboard": false,
  "language": "de",
  "whisper_initial_prompt": "Bolt, Topsort, GMV, INT, Jens, Fabrizio"
}
```

### Example: clipboard only, no auto-paste

```json
{
  "paste_mode": "never",
  "clipboard": true
}
```

---

## Logs

| File | Contents |
|------|----------|
| `logs/app.log` | application events (startup, transcriptions, errors) |
| `logs/stdout.log` | stdout + stderr from the Python process |
| `logs/launchd.*.log` | launchd stdout / stderr |

Menu ▸ **Open logs** opens `logs/app.log` directly.

---

## Uninstall

```bash
./uninstall.sh        # stop + remove the LaunchAgent and the app bundle
rm -rf "$(pwd)"       # remove everything (run from the repo folder)
```

---

## Design notes

- **Hotkey**: native Carbon `RegisterEventHotKey` via `ctypes`. No keystroke
  listener, no Input Monitoring permission. Escape-to-abort is a second hotkey
  armed only while recording or transcribing, routed by event id so it never
  fires the toggle handler.
- **Recording**: blocking `sounddevice` reads on a worker thread. A real-time
  Python callback caused a CoreAudio/GIL deadlock (`AudioOutputUnitStop` hung the
  main thread); the blocking approach avoids it entirely.
- **Transcription**: `mlx-whisper` in-process on a background thread. Progress
  uses Whisper's true per-window fraction on clips > 30s; for shorter clips it
  uses a time estimate that self-calibrates from your machine's actual speed.
- **UI**: borderless `NSVisualEffectView` with a rounded mask image and semantic
  colors (`labelColor` / `secondaryLabelColor`), reads correctly in light and dark
  themes with no white corners.
- **Menu-bar icon**: on macOS 26 (Tahoe), launching through the app bundle leaves
  the status item unregistered (windows draw, icon never attaches). The LaunchAgent
  runs the venv Python directly; the bundle executable just `launchctl kickstart`s
  that agent so Spotlight / Finder launches hand off to the working path. The icon
  is an SF Symbol set on the status item's button (an emoji title does not render
  on Tahoe).
- **Privacy**: audio is processed entirely on-device. The only network calls are
  the one-time model download from Hugging Face and a version check on each
  transcription (Hugging Face metadata; can be made offline by pinning the model
  path to a local directory).
