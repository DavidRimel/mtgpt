#!/usr/bin/env bash
# Build mtgpt.zip from committed files only: local decks and notes never ship.
set -euo pipefail
if ! root=$(git rev-parse --show-toplevel 2>/dev/null); then
  echo "make-zip.sh: not inside a git repository; run this from a git checkout" >&2
  exit 1
fi
cd "$root"
if [ -n "$(git status --porcelain -- mtgpt/data/card_rules.json)" ]; then
  echo "warning: mtgpt/data/card_rules.json has uncommitted changes; they will not be in the zip" >&2
fi
git archive --format=zip --prefix=mtgpt/ -o mtgpt.zip HEAD
echo "wrote $(pwd)/mtgpt.zip"
