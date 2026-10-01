# Session handoff — 2026-10-01

## State

- `main` — the reviewed core toolkit, public, 306 tests. Merged and pushed.
- `layer1` — source expansion (`find`, `import`, `compare`, 46 Tagger tags) plus the
  fix wave answering the whole-branch review. 520 tests. Pushed, **not yet merged**.
- `decks/jodah-archmage-eternal-bracket4.txt` — a worked example, verified by the toolkit.

## What is unfinished

`layer1`'s last commit answers a review that blocked the merge. It has **not been
re-reviewed**. Before merging, re-run the scoped re-review on the fix range and confirm:

1. Recursion **precision** improved, not just recall. The original blocker was that
   `agreement_rate` measured recall only and was structurally blind to false positives —
   135 shipped behind a number that could only move the flattering way.
2. `Cry of the Carnarium` (graveyard hate), the Skaab/delve class (graveyard as cost),
   the Increasing cycle (flashback), and `Rekindling Phoenix` (self-return) are all OUT.
3. `Breach the Multiverse` and the 21 existing positives are all IN.
4. `otag:mass-land-denial` is wired up and `SKILL.md` no longer publishes the stale
   `recursion 0.37` figure or the claim that Reanimate is untagged.

## Chrome automation

`.wslconfig` now sets `networkingMode=mirrored`, applied on the next `wsl --shutdown`.
After that, Chrome on Windows launched with `--remote-debugging-port=9222` is reachable
from WSL at `127.0.0.1:9222`, and the `chrome-devtools` MCP server can drive it.

Node v24.21.0 is installed at `~/.local/bin` (no sudo), and `chrome-devtools-mcp@1.9.0`
is pre-installed under `~/.claude/plugins/data/chrome-devtools-mcp`.

## Decisions

`docs/superpowers/decisions-2026-10-01.md` — 51 decisions with reasoning and what each
costs if wrong. Read before overturning any threshold, severity, or deferral.
