#!/bin/bash
# Chipper Studio installer for macOS (Apple silicon or Intel). Double-click it, or run: ./install-mac.command
cd "$(dirname "$0")" || exit 1
set -e

if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew (it will ask for your Mac password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"; else eval "$(/usr/local/bin/brew shellenv)"; fi

brew install python@3.12 ffmpeg cloudflared

PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

echo
read -r -p "Make captions on this Mac with Whisper (about 2 GB download)? [y/N] " WH
if [[ "$WH" =~ ^[Yy]$ ]]; then python -m pip install -r requirements-captions.txt; fi

chmod +x run-mac.command
echo
echo "Done. Start Chipper Studio by double-clicking run-mac.command"
