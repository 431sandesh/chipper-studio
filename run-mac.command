#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PATH="$PWD/bin:$HOME/.local/bin:$PATH"
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
if [ ! -f .venv/bin/activate ]; then echo "Chipper isn't installed yet. Run: bash install-mac.command"; exit 1; fi
source .venv/bin/activate
python app.py
