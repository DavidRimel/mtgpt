---
name: mtgpt
description: Use when the user wants to build, import, tune, or continue a Magic the Gathering Commander/EDH deck in this repo - lists saved deck projects, starts new ones (import a link, paste a list, or build from scratch), then always runs scan, research, and tune with the goldfish simulator and a fixed scorecard. Triggers on "EDH", "Commander deck", "my decks", "new deck", "tune my deck", "goldfish", "decklist", "moxfield", "archidekt", "what should I cut".
---

# mtgpt — Commander deck projects

Run commands from the repo root as `python3 -m mtgpt.cli <command>`. Output is
JSON: `{"ok": true, "command": ..., "data": ...}` or `{"ok": false, ..., "error": ...}`.

## First run

`python3 --version` must be 3.12 or newer. On WSL Ubuntu 22.04 (Python 3.10):

    sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.12

then use `python3.12` wherever this skill says `python3`. Do not continue on an older Python.

## Rules that never bend

1. **Decks are save files.** Everything about a deck lives in `decks/<slug>/`, which git
   ignores. Never `git add` anything under `decks/`. The only deck work that is committed
   is card rules (`mtgpt/data/card_rules.json`).
2. **Every card comes from the toolkit** (Scryfall-verified), never from memory.
3. **Never infer the win state or the commander's "thing".** Ask.
4. **Scan before every goldfish.** No sim over unreviewed cards.
5. **Commander Spellbook: look up the commander only** (`card-combos "<commander>"`).
   Never submit a deck to any site.
6. **Goldfish numbers compare versions; they do not predict real games.** Say so in every report.

## Start of every session

1. `python3 -m mtgpt.cli project list` — show name, commander, bracket, best version, its
   wins-by-target-round, last updated. Ask: continue a deck, or start a new one?
2. **Continue:** `python3 -m mtgpt.cli project status <slug>`. Read `playtest_notes` first and
   let them steer this session (adjust the goal's disruption or `opponent_win`; aim at what
   went wrong). Resume at the project's `stage`.
3. **New:** ask the deck's name, the target bracket, and where the list comes from:
   - **A link.** Archidekt: `python3 -m mtgpt.cli import "<url>"` and write its `decklist`
     to a scratch file. Moxfield or another site: load the `claude-in-chrome` skill, open the
     deck in a tab, and read the list. For Moxfield, from a moxfield.com tab fetch
     `https://api2.moxfield.com/v3/decks/all/<id>` with JavaScript and build the list from
     its commanders and mainboard. If Chrome is not connected or the read fails, ask the user
     to paste the list (Moxfield: More → Export → Copy for Moxfield).
   - **Paste.**
   - **From scratch.** Ask the commander, bracket, and the theme or playstyle (no budget —
     decks are proxied). Do stage 2's research first, draft a 99 with multi-job cards first,
     `validate` and `audit` until legal, and show the user the draft for approval.

   Then `python3 -m mtgpt.cli project new --name "<name>" --bracket <N> --file <list> [--source <url>]`.
4. **Gate:** `python3 -m mtgpt.cli validate --file decks/<slug>/v1.txt` and
   `python3 -m mtgpt.cli bracket --file decks/<slug>/v1.txt --target <N>` must pass. If not,
   propose fixes, and with the user's OK `project save` the fixed list.
5. **Goal:** ask the win state and the commander's thing; write `decks/<slug>/goal.json`
   (`references/goldfish.md`). Add `"target_round"` only if the user wants other than the
   bracket default (1–2 → 7, 3 → 5, 4 → 4, 5 → 3). Show it; confirm.
   `python3 -m mtgpt.cli project stage <slug> scan`.

## Stage 1 — scan

    python3 -m mtgpt.cli goldfish-scan --file decks/<slug>/<best>.txt --goal decks/<slug>/goal.json

Record a verdict for every `needs_review` card with `python3 -m mtgpt.cli card-rule`
(`parsed` / `override` / `ignored` — `references/goldfish.md`, step 0). Space the calls out:
Scryfall rate-limits bursts. A card that matters but the engine cannot express is a
**sim gap**: `project log` it, explain what the card does and what the sim would need, and
ask whether to build the engine feature now (test-first) or continue with the gap noted.
Then `project stage <slug> research`.

## Stage 2 — research

Follow `references/research.md`: EDHREC (`synergy`, `themes`, `compare`), the commander's
Spellbook combos saved to `decks/<slug>/combos.json`, `find` packages, and primers. Write the
deck's jobs and verified candidates to `decks/<slug>/research.md`, multi-job first, and
pre-scan the candidates. Then `project stage <slug> tune`.

## Stage 3 — tune

Follow `references/tuning-loop.md`: baseline `scorecard` (record it with `project best ... --primary`; re-score and re-record whenever `goal.json` changes), then one swap at a time — save,
judge against the best version, act on the verdict, log it — with a checkpoint after 10
swaps or 3 non-keeps in a row. The user chooses continue, change direction, or stop.

## Finishing

Land pass, pilot spot-check, final report with a paste-ready list
(`references/tuning-loop.md`, Finishing). Then `project stage <slug> finish`.

## Real games

When the user says how the deck played: `python3 -m mtgpt.cli project note <slug> "<what happened>"`.

## Sharing

- A friend's new card rules: `python3 -m mtgpt.cli card-rule-merge <their card_rules.json>`;
  show the user each conflict and let them choose (`card-rule` to set the winner).
- A zip for a friend: `scripts/make-zip.sh` (committed files only — no decks).

## References

- `references/tuning-loop.md` — scorecard, verdicts, the loop, finishing
- `references/research.md` — sources, deck jobs, the multi-job judgment
- `references/goldfish.md` — goal file format, engine overrides, pilot mode
- `references/toolkit.md` — every command, auditing, brackets, combos, reading the numbers
- `references/brackets.md`, `references/deckbuilding-hygiene.md`, `references/sources.md`
