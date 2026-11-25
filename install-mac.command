#!/bin/bash
# Chipper Studio installer for macOS (Apple silicon and Intel). Double-click it, or run: bash install-mac.command
# Nothing here needs your admin password on Intel Macs; Apple silicon Macs use Homebrew for FFmpeg and cloudflared.
cd "$(dirname "$0")" || exit 1
set -e
ARCH="$(uname -m)"
mkdir -p bin
export PATH="$PWD/bin:$HOME/.local/bin:$PATH"

latest_zip() {   # newest FFmpeg/ffprobe zip from evermeet.cx (Intel builds, with libass for captions)
  local u
  u="$(curl -fsSL "https://evermeet.cx/ffmpeg/info/$1/release" 2>/dev/null | grep -o '"url":"[^"]*\.zip"' | head -1 | cut -d'"' -f4)"
  echo "${u:-https://evermeet.cx/ffmpeg/$1-9.1.zip}"
}

if [ "$ARCH" = "arm64" ]; then
  echo "Apple silicon Mac: installing FFmpeg and cloudflared with Homebrew..."
  if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ]; then
    echo "Installing Homebrew (it will ask for your Mac password)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  eval "$(/opt/homebrew/bin/brew shellenv)"
  brew install ffmpeg cloudflared
else
  echo "Intel Mac: downloading FFmpeg and cloudflared into the Chipper folder..."
  for b in ffmpeg ffprobe; do
    if [ ! -x "bin/$b" ]; then
      curl -fL --progress-bar -o "/tmp/chipper_$b.zip" "$(latest_zip $b)"
      unzip -o -q "/tmp/chipper_$b.zip" -d bin && rm -f "/tmp/chipper_$b.zip"
    fi
  done
  if [ ! -x bin/cloudflared ]; then
    curl -fL --progress-bar https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz | tar -xz -C bin
  fi
  chmod +x bin/* 2>/dev/null || true
  xattr -dr com.apple.quarantine bin 2>/dev/null || true
fi

# Python 3.12 in the Chipper folder (works on every Mac, no admin password)
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (gets Python for Chipper)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
[ -x .venv/bin/python ] || uv venv --seed --python 3.12 .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

echo
read -r -p "Make captions on this Mac with Whisper (about 2 GB download)? [y/N] " WH
if [[ "$WH" =~ ^[Yy]$ ]]; then python -m pip install -r requirements-captions.txt; fi

chmod +x run-mac.command
echo
ffmpeg -version | head -1
echo
echo "Done. Start Chipper Studio with: bash run-mac.command  (or double-click run-mac.command)"
