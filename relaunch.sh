#!/usr/bin/env bash
# Relaunch trnscrb via launchd.
# Reinstalls code, syncs ANTHROPIC_API_KEY into the plist, and restarts the service.
set -euo pipefail

LABEL="io.trnscrb.app"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- 1. Install updated code ---
echo "Installing trnscrb..."
cd "$REPO_DIR"
uv pip install -e .

# --- 2. Sync ANTHROPIC_API_KEY into plist if set in shell ---
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    current=$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:ANTHROPIC_API_KEY" "$PLIST" 2>/dev/null || true)
    if [[ "$current" != "$ANTHROPIC_API_KEY" ]]; then
        echo "Updating ANTHROPIC_API_KEY in plist..."
        # Ensure EnvironmentVariables dict exists
        /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables dict" "$PLIST" 2>/dev/null || true
        /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:ANTHROPIC_API_KEY $ANTHROPIC_API_KEY" "$PLIST" 2>/dev/null \
            || /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:ANTHROPIC_API_KEY string $ANTHROPIC_API_KEY" "$PLIST"
    fi
fi

# --- 3. Reload the service ---
echo "Reloading launchd service..."
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# --- 4. Verify ---
sleep 1
if launchctl list | grep -q "$LABEL"; then
    pid=$(launchctl list | grep "$LABEL" | awk '{print $1}')
    echo "trnscrb running (PID $pid)"
else
    echo "WARNING: service not found after reload — check /tmp/trnscrb.err"
    exit 1
fi
