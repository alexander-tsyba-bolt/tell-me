#!/bin/bash
# Stop and remove the Tell Me! LaunchAgent. Leaves project files + venv in place.
set -euo pipefail
LABEL="com.atsyba.tellme"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
pkill -f "$(cd "$(dirname "$0")" && pwd)/app.py" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "/Applications/Tell Me!.app" "$HOME/Applications/Tell Me!.app"
echo "Removed $LABEL and the Tell Me!.app bundle. Project files and venv kept; delete this folder to fully remove."
