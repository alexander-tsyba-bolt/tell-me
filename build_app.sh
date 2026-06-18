#!/bin/bash
# Build "Tell Me!.app" into ~/Applications so it is launchable from Spotlight,
# Finder, and the Dock. It is a thin LSUIElement launcher whose executable runs
# the venv python on app.py in this project folder, so the project folder must
# stay where it is. The bundle gives macOS a stable identity + name for the
# Microphone / Accessibility permission grants.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${TELLME_APPS_DIR:-/Applications}"   # install.sh sets this; fall back if /Applications is not writable
[ -w "$APPS" ] || APPS="$HOME/Applications"
APP="$APPS/Tell Me!.app"

mkdir -p "$APPS"
rm -rf "$APP" "$DIR/Tell Me!.app"  # rebuild target + clear any stale project-dir bundle
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Tell Me!</string>
  <key>CFBundleDisplayName</key><string>Tell Me!</string>
  <key>CFBundleIdentifier</key><string>com.atsyba.tellme</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>run</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSUIElement</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key><string>Tell Me! records your microphone to transcribe speech locally on-device.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/run" <<RUN
#!/bin/bash
# Launching the app through the bundle (open -> bash -> exec python) leaves the
# menu-bar status item unregistered on macOS 26. So the bundle does not run
# Python itself; it hands off to the LaunchAgent, which launches the venv Python
# directly (the launch that registers the icon). This makes Spotlight/Finder/Dock
# launches work too. No -k: a plain kickstart starts it if quit and is a no-op if
# already running, avoiding a kill/restart race with the single-instance lock.
exec launchctl kickstart "gui/\$(id -u)/com.atsyba.tellme"
RUN
chmod +x "$APP/Contents/MacOS/run"

# App icon: render the iconset with the venv python, then build AppIcon.icns.
ICONSET="$(mktemp -d)/AppIcon.iconset"
if "$DIR/.venv/bin/python" "$DIR/make_icon.py" "$ICONSET" >/dev/null 2>&1 \
   && iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null; then
  echo "app icon generated"
else
  echo "warn: app icon not generated (needs venv pyobjc + iconutil)"
fi
rm -rf "$(dirname "$ICONSET")"

# Ad-hoc sign so TCC keeps a stable identity for this bundle.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "warn: codesign skipped"
# Nudge Spotlight to index it now.
mdimport "$APP" >/dev/null 2>&1 || true
echo "built $APP"
