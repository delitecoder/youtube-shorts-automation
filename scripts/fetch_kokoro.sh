#!/usr/bin/env bash
# Downloads the Kokoro-82M ONNX model (Apache-2.0) used as the offline TTS fallback and verifies
# pinned SHA-256 checksums. ~120 MB. Usage: scripts/fetch_kokoro.sh [target_dir]
set -euo pipefail
DIR="${1:-${KOKORO_MODELS_DIR:-runtime/models}}"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
mkdir -p "$DIR"
fetch() {  # name sha256
  if [ -f "$DIR/$1" ] && echo "$2  $DIR/$1" | sha256sum -c --quiet - 2>/dev/null; then return; fi
  curl -fsSL --retry 4 -o "$DIR/$1.part" "$BASE/$1"
  echo "$2  $DIR/$1.part" | sha256sum -c --quiet - || { echo "checksum mismatch for $1" >&2; rm -f "$DIR/$1.part"; exit 1; }
  mv "$DIR/$1.part" "$DIR/$1"
}
fetch kokoro-v1.0.int8.onnx 6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb
fetch voices-v1.0.bin       bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
echo "Kokoro model ready in $DIR"
