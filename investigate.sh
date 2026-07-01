#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: ./investigate.sh <mint> <developer-wallet>"
  exit 1
fi

python analyze_wallet.py \
  --mint "$1" \
  --developer "$2"

