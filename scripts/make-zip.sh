#!/usr/bin/env bash
# Build mtgpt.zip from committed files only: local decks and notes never ship.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git archive --format=zip --prefix=mtgpt/ -o mtgpt.zip HEAD
echo "wrote $(pwd)/mtgpt.zip"
