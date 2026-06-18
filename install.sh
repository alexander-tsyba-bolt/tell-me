#!/bin/bash
# Tell Me! installer. Sets up the Python venv, dependencies, the app bundle, and
# a LaunchAgent so it runs at login. Run this yourself so you are present to
# grant the macOS permission prompts on first launch.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.atsyba.tellme"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"
PYVER="3.12"

echo "==> Tell Me! installer"

# 1. Apple Silicon macOS only (MLX requirement).
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "Tell Me! needs an Apple Silicon Mac (MLX)." >&2
  exit 1
fi

# 2. portaudio: the mic-capture backend for sounddevice.
if ! brew list portaudio >/dev/null 2>&1; then
  echo "==> installing portaudio (Homebrew)"
  brew install portaudio
fi

# 3. Python venv on a wheel-compatible interpreter (avoid 3.14).
mkdir -p "$DIR/logs"
if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "==> creating venv (.venv) on Python $PYVER"
  if command -v uv >/dev/null 2>&1; then
    uv venv "$DIR/.venv" --python "$PYVER"
  elif command -v "python$PYVER" >/dev/null 2>&1; then
    "python$PYVER" -m venv "$DIR/.venv"
  else
    echo "Need 'uv' or 'python$PYVER'. Install uv (https://docs.astral.sh/uv/) or 'brew install python@$PYVER'." >&2
    exit 1
  fi
fi

# 4. Dependencies.
echo "==> installing dependencies"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$DIR/.venv/bin/python" -r "$DIR/requirements.txt"
else
  "$DIR/.venv/bin/python" -m pip install --upgrade pip
  "$DIR/.venv/bin/python" -m pip install -r "$DIR/requirements.txt"
fi

# 5. Config: create from template, never clobber an existing one.
if [ ! -f "$DIR/config.json" ]; then
  cp "$DIR/config.example.json" "$DIR/config.json"
  echo "==> created config.json from template"
fi

# 6. Build the app bundle into /Applications (or ~/Applications if not writable),
#    so it is launchable from Spotlight / Finder / Dock.
APPS="/Applications"
[ -w "$APPS" ] || { APPS="$HOME/Applications"; mkdir -p "$APPS"; }
export TELLME_APPS_DIR="$APPS"
"$DIR/build_app.sh"

# 7. LaunchAgent: run at login, relaunch on crash, but not on a clean Quit.
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>$DIR/app.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err.log</string>
</dict>
</plist>
PL

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
pkill -f "$DIR/app.py" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl enable "gui/$UID_NUM/$LABEL" 2>/dev/null || true

cat <<DONE

==> Tell Me! installed and running. Look for the 🎤 icon in the menu bar.

Grant these in System Settings > Privacy & Security (add "Tell Me!", or the
"Python" entry if that is what macOS shows):
  - Microphone     prompts on your first recording
  - Accessibility  needed for auto-paste (skip if paste_mode = "never")

The first transcription downloads the Whisper model (~1.6 GB) once.
Toggle recording with the hotkey in config.json (default: Ctrl+Opt+Cmd+Space).

"Tell Me!" is now in ~/Applications, so you can launch it from Spotlight, Finder,
or the Dock (a single-instance lock means that never starts a second copy).
After editing config.json, use the menu's "Reload config" to apply it instantly.
DONE
