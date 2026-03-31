#!/usr/bin/env bash
# Restart trnscrb — useful after code changes or env var updates
set -e

pkill -f "trnscrb" 2>/dev/null && echo "Stopped trnscrb" || echo "trnscrb was not running"
sleep 1
source ~/.zshrc 2>/dev/null
trnscrb start &
echo "trnscrb restarted"
