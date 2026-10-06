#!/bin/bash
# One-time (idempotent) setup of the Python environment the pipeline runs in.
# Usage: bash <this skill's base directory>/scripts/setup.sh
set -e
V="$HOME/.cache/swolverine-product-photos/venv"
if [ -x "$V/bin/python" ] && "$V/bin/python" -c "import numpy, cv2, tifffile, imagecodecs, PIL, scipy, psd_tools" 2>/dev/null; then
  echo "ready: $V/bin/python"; exit 0
fi
mkdir -p "$(dirname "$V")"
/usr/bin/python3 -m venv "$V"
"$V/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$V/bin/pip" install -q numpy opencv-python-headless tifffile imagecodecs pillow scipy psd-tools
"$V/bin/python" -c "import numpy, cv2, tifffile, imagecodecs, PIL, scipy; print('ready:', '$V/bin/python')"
