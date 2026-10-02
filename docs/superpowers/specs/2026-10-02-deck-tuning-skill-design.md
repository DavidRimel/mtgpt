# Deck-tuning skill — design

Date: 2026-10-02. Status: approved in conversation; awaiting written-spec review.

## Goal

Turn mtgpt into a skill a friend can use by unzipping the repo and opening Claude Code in it.
The skill manages deck projects (list past decks, start a new one), gets a list in (import,
paste, or build from scratch), then always runs the same three-stage tuning process:

1. **Scan** — write sim rules for every card the shared library has not reviewed.
2. **Research** — how this commander is usually built (EDHREC, Commander Spellbook lookups for
   the commander, curated primers), producing a verified candidate list.
3. **Tune** — sim a baseline, find the weakest target, swap one card at a time, keep what helps.

Tuning targets, in the user's words: consistent turn-5 wins, more protection, the commander
ramped out on or before curve, interaction usually in hand, a land base with mostly untapped
lands producing the colors the pips need, and a preference for cards that do two or more of the
jobs this deck wants.

## Constraints

- **Decks are local save files.** Everything about a deck lives under `decks/`, which git
  ignores. Only card logic (`mtgpt/data/card_rules.json`) and code are ever committed.
- **Distribution is a zip of the repo**, built with `git archive` so local decks never ship. The
  friend runs Windows + WSL + Claude Code, same as the author, and opens Claude in the repo root.
- **Commander Spellbook is read-only and commander-scoped.** Research looks up combos for the
  commander (`card-combos <commander>`). Nothing is ever submitted to Spellbook.
- **Stdlib-only runtime, Python ≥ 3.12**, as today. No `pip install` required to use the skill.
- Existing rules carry over: never infer the win state or the commander's "thing" (ask); scan
  before every goldfish; find candidates with the toolkit, never from memory; goldfish numbers
  compare versions, they do not predict real games.

## 1. Deck projects

### Layout

```
decks/<slug>/
  project.json   commander, target bracket, source URL, best version, created/updated
  goal.json      win state and the commander's "thing" (existing goal format)
  v1.txt         starting list; every later version is a new immutable file
  v2.txt …
  research.md    deck jobs list + candidates by job, with sources and the multi-job flag
  combos.json    `card-combos <commander>` output, cached at research time for the floor check
  log.md         every swap tried: scorecard, verdict, reason; sim gaps; checkpoints
```

`project.json`:

```json
{
  "name": "Hapatra",
  "commander": "Hapatra, Vizier of Poisons",
  "bracket": 3,
  "source": "https://moxfield.com/decks/...",
  "best": "v8",
  "stage": "tune",
  "created": "2026-10-01",
  "updated": "2026-10-02"
}
```

`stage` is one of `scan`, `research`, `tune`, `finish`, and lets a session resume where the last one stopped.

### Session start

1. Run `project list`: a table of name, commander, bracket, best version, its win rate by the
   target round (from the last scorecard in the log), and last-updated date. Ask: continue a deck or start a new one?
2. **Continue** — `project status <slug>` shows stage, best version, and the log tail; resume there.
3. **New** — ask name and target bracket, then where the list comes from:
   - **Import a link.** Archidekt via the existing `import`. Moxfield and other sites via the
     Chrome MCP: open the deck in a tab and read the list from the page (for Moxfield, the
     `api2.moxfield.com/v3/decks/all/<id>` JSON fetched from a moxfield.com tab). If Chrome is
     not connected, ask the user to paste the list.
   - **Paste.**
   - **From scratch.** Ask commander, bracket, and the theme or playstyle wanted (no budget or
     collection limits — decks are proxied). Draft a
     legal 99 from EDHREC average deck and themes (`compare`, `themes`, `synergy`), Spellbook
     combos for the commander, and `find` packages, multi-job cards first. Validate and audit;
     the user approves the draft before it becomes `v1`.
4. **Gate before tuning:** `v1` must pass `validate` (legal, 100 cards, color identity) and
   `bracket` at the target. Ask the user the win state and the commander's "thing"; write `goal.json`
   and show it for confirmation.

### Target round

The primary metric is wins by a **target round**, stored in `goal.json` as `"target_round"`.
Defaults by bracket when the user gives none: bracket 1–2 → 7, bracket 3 → 5, bracket 4 → 4,
bracket 5 → 3. "Turn-5 wins" elsewhere in this spec means wins by the target round.

### Version names

`project save` always writes the next integer above the highest existing version (`v11b` counts
as 11, so the next save is `v12`). Hand-named variants from migration are kept as-is.

### Migration

A one-off, uncommitted script moves the author's existing flat files
(`decks/hapatra-bracket3-vN.txt` + `.goal.json`, the Jodah lists) into project folders,
preserving version history and setting `best`. Four projects result:

| Project | Versions | Best |
|---|---|---|
| `hapatra` (bracket 3) | v1 (the original list), v2 … v11, v11b; v4a/v4b and v6a/v6b kept as named | v8 |
| `jodah-big-spells-b3` | v1 (`jodah-big-spells-bracket3.txt`), v5 | v5 |
| `jodah-big-spells-b4` | v1 | v1 |
| `jodah-archmage-eternal-b4` | v1 (no goal file; one is written at its first tune) | v1 |

Each version's goal file becomes that project's `goal.json` from its best version; the others are
kept beside their lists as `vN.goal.json` for history. `hapatra-bracket3-v8.moxfield.txt` is the
same list as v8 in Moxfield format and is dropped.

## 2. Stage 1 — scan and write sim rules

- `goldfish-scan` the best version with its goal. Every card in `needs_review` gets a verdict
  recorded with `card-rule`: `parsed`, `override` (expressible with existing engine fields), or
  `ignored` (only affects opponents).
- A card that matters but the engine cannot express is a **sim gap**: logged in `log.md`, explained
  to the user (what the card does, what the sim would need), and the user is asked whether to build
  the engine feature now (test-first, as X tutors were) or continue with the gap noted.
- Scryfall calls are throttled; 429s retry with backoff.

## 3. Stage 2 — research

- **EDHREC:** `synergy` (high-synergy cards), `themes` (common builds), `compare` (diff against the
  average deck: what most builds play that this list does not).
- **Commander Spellbook:** `card-combos <commander>` only.
- **Other sites:** web search for primers and tuned lists from sources listed in
  `references/sources.md`; sites that block scripts are read through Chrome.
- **Deck jobs.** From the goal, the commander's text, and the research, write the list of jobs this
  deck needs at the top of `research.md` (for a landfall commander: ramp, land recursion, landfall
  triggers, protection, removal…).
- **Multi-job flag.** A candidate is multi-job when it covers two or more of *this deck's* jobs. It
  is Claude's judgment against the jobs list, informed by `classify` tags — not a mechanical
  "is modal/MDFC" test. Example: a creature that replays lands from the graveyard and has a
  landfall trigger is multi-job in a landfall deck and not in a spellslinger deck.
- **Verification.** Every candidate is resolved on Scryfall (`card`/`classify`), checked for color
  identity, bracket legality, and Game Changer count.
- **Pre-scan.** Candidates get sim rules here (stage-1 process), so the loop never stalls.
- **Output** `research.md`: candidates grouped by job; each with sources, EDHREC inclusion rate,
  multi-job flag (and which jobs), and a suggested cut. Multi-job first within each group.

## 4. Stage 3 — the tuning loop

### Scorecard

`scorecard` runs the goldfish (1000 games, fixed seed, the deck's goal including disruption and
`opponent_win`) and reduces it to:

| Target | Measured by | Target level |
|---|---|---|
| Wins by target round (primary) | `win.win_by_round[target_round - 1]`; total win rate alongside | — (maximize) |
| Commander on curve | `commander.on_curve_rate`; mana available on the commander turn | ≥ 70% |
| Interaction in hand | `thing.covered_rate`; `opponent_win.answered_rate`; `loss.by_reason.opponent_win` | covered ≥ 50% |
| Protection | `disruption.stopped_by_protection_rate`; `disruption.win_rate_after_event` | — |
| Opening hands | `setup.mulligan_rate`, with its causes (too few lands, two lands without cheap ramp, flood — the sim's keep rule) | ≤ 25% |
| Land base (new) | untapped-land share; per-color sources vs pip demand; `commander.late_reasons.color_screw` | untapped ≥ 80%, no short color |

`scorecard --file A --file B` scores both on matched seeds and returns a verdict:

- **keep** — primary rises by ≥ 1.5 points and no guard breaks its tolerance.
- **revert** — primary falls, or a guard breaks.
- **mixed** — anything else; Claude decides and writes the reason in the log, leaning toward
  multi-job cards and the weakest target.
- **close call** — when the primary moved by less than 3 points either way, the comparison is
  re-run at 3000 games before the verdict is final.

Guard tolerances: on-curve −3 points, opponent-win loss rate +2 points, covered rate −3 points,
mulligan rate +3 points, no color newly short. Target levels and tolerances live in one table in
`scorecard.py`.

**Noise floor.** A one-time calibration (part of implementation, recorded in
`references/tuning-loop.md`) scores a deck against itself and against a swap of two cards the sim
treats identically, at 1000 and 3000 games, to confirm 1.5 points is above noise. If it is not,
the threshold is raised to what the calibration shows.

### Card impact

`scorecard` also reports, per card in the deck, from the same games:

- **drawn win rate − not-drawn win rate** (by the target round),
- **dead rate** — fraction of games it was drawn and never cast/played,
- **cast rate** and median turn cast.

This needs the engine to record, per game, which cards were drawn and which were cast; the
goldfish runner aggregates it. Cut choices in the loop use this data, not intuition. Cards whose
value the sim cannot see (`ignored` rules, removal held for `opponent_win`) are marked
"not measurable" rather than shown as dead.

### Floors (checked before a swap is simmed)

A proposed version is rejected without simming when it:

- drops any `audit` category (lands, ramp, draw, removal, board wipes) below its target band's
  minimum, or
- fails `bracket` at the project's target with an error the previous version did not have — an
  extra Game Changer, mass land denial, or (brackets 1–2) a newly completed two-card combo.
  Two-card combos come from the commander's Spellbook combos cached at research time
  (`decks/<slug>/combos.json`), never from submitting the deck. At bracket 3 a newly completed
  two-card combo is a warning shown with the verdict, matching `brackets.py`'s `late_only` rule.

This stops the loop from trading away interaction the goldfish undervalues.

### Land-base numbers

- **Untapped share:** fraction of lands that do not enter tapped, from the sim's existing
  `enters_tapped`/`fetch_tapped` parse (`effects.py`).
- **Colors vs pips:** per color, `audit`'s existing `PipReport` (`sources` against `required`);
  a color whose `ok` is false is "short".

### Loop

1. **Baseline:** scorecard the best version; name the weakest target against the target levels.
2. **Pick a swap:** in — a `research.md` candidate that addresses the weakness, multi-job preferred;
   out — chosen from card impact: the lowest-impact single-job card that is not a combo piece, not
   "not measurable" interaction, and not a land the colors need.
3. **Floors:** reject the swap if it breaks a floor (above); pick again.
4. **Test:** `project save` writes the next version; scorecard it against the best version; apply
   the verdict (`project best` on keep); log it.
5. **One swap at a time.** Pairs only when the cards need each other (two combo pieces).
6. **Land count:** once per checkpoint round, test ±1 land (a land for the lowest-impact spell, or
   the reverse) through the same verdict.
7. **Checkpoint** after 10 swaps tried or 3 consecutive non-keeps: report before/after scorecards,
   kept swaps and why, sim gaps hit. The user picks continue, change direction, or stop.

### Finishing a deck

When the user stops the loop:

1. **Land pass.** A focused pass swapping tapped lands for untapped ones producing the same colors
   (and fixing any short color), each through the verdict.
2. **Pilot spot-check.** Claude pilots 2–3 games of the final version (existing pilot mode, same
   seed and game numbers as auto games) and reports, per game, where its line beat or lost to the
   heuristic on the same deal. A large gap is logged as a likely heuristic blind spot.
3. **Final report:** best version's scorecard against `v1`, every kept swap and why, sim gaps,
   and a paste-ready list for Moxfield/Archidekt import.

### Real-game notes

After the user plays the deck for real, `project note <slug> "<text>"` appends a dated playtest
note to `log.md` ("flooded twice, wiped on turn 6"). On the next session the skill reads the notes
first and uses them to adjust the goal (disruption rates, `opponent_win` timing) and to aim the next
tuning pass at what actually went wrong. Notes are the correction for where the goldfish is
optimistic.

Every report carries the goldfish caveat: numbers compare versions, they do not predict games.

## 5. Packaging and code

### Skill

- Move `skills/mtgpt/` to `.claude/skills/mtgpt/` so it loads as a project skill. Remove
  `.claude-plugin/` and the plugin install line from the README; the zip is the distribution.
- `SKILL.md` (~150 lines) holds only the workflow: session start, stages 1–3, checkpoints, and the
  honesty rules. Detail moves to references Claude opens on demand:
  - `references/goldfish.md` — goal format, engine fields, pilot mode (from today's SKILL.md)
  - `references/research.md` — sources, the deck-jobs list, the multi-job judgment
  - `references/tuning-loop.md` — scorecard targets, verdict rule, checkpoint report format
  - existing `brackets.md`, `deckbuilding-hygiene.md`, `sources.md`
- **First-run check:** `python3 --version` ≥ 3.12, else exact steps for WSL Ubuntu 22.04
  (deadsnakes PPA). The skill runs `python3 -m mtgpt.cli` from the repo root.
- `scripts/make-zip.sh`: `git archive --format=zip -o mtgpt.zip HEAD`.

### New modules (each independently testable, offline)

| Unit | Does | Depends on |
|---|---|---|
| `mtgpt/projects.py` + `project list\|new\|status\|save\|best\|note` | deck folders, `project.json`, next-integer versioning, best pointer, log append, playtest notes | filesystem only |
| `mtgpt/scorecard.py` + `scorecard` | reduce a goldfish report to targets; compare two; verdict incl. close-call re-run at 3000 games | goldfish report dicts, audit |
| floors (in `scorecard.py`) | reject a version below an `audit` band minimum or failing `bracket`, before simming | `audit`, `brackets` |
| card-impact tracking (goldfish engine + runner) | record per game which cards were drawn and cast; aggregate drawn/not-drawn win delta, dead rate, cast turn | `goldfish/engine.py`, `goldfish/run.py` |
| mulligan causes (goldfish engine + runner) | why each mulligan happened: too few lands, two lands without cheap ramp, flood | `goldfish/engine.py`, `goldfish/run.py` |
| land-base metrics (in `scorecard.py`) | untapped share, colors vs pips | `effects.py`, `audit` |
| `card-rule-merge <file>` (in `card_rules.py`) | add rules not present; list conflicts, change nothing for them | card rules library |

`decks/` stays the default root; `project` commands take `--root` for tests.

### Errors

- Illegal or incomplete deck: stop before tuning with the reason.
- Scryfall 429: retry with backoff.
- Unmodelable card: sim gap logged + question to the user.
- Failed import / Chrome not connected: fall back to pasting.
- `project new` on an existing slug: refuse, suggest `project status`.

### Testing

- New code test-first against recorded fixtures and `tmp_path` deck roots; no network.
- `scorecard` verdict tests cover keep, revert (primary drop), revert (guard break), mixed, and
  close call.
- Floor tests: a swap dropping removal below its band is rejected; a swap adding a fourth Game
  Changer to a bracket-3 deck is rejected.
- Card-impact tests on a small deterministic deck: a card that is never castable shows dead rate 1;
  an `ignored` card shows "not measurable".
- `project save` naming: after `v11b`, the next save is `v12`.
- The noise-floor calibration runs once and its numbers are recorded in `references/tuning-loop.md`.
- `card-rule merge` tests cover new rules, identical rules, and conflicts.
- Before calling it done, run the rewritten skill end to end on the migrated Hapatra project.

## Out of scope

- A fully automated Python tuner (approach C).
- Plugin/marketplace distribution.
- Submitting anything to Commander Spellbook, Moxfield, or any other site.
- Opponent modeling beyond the existing disruption dice and `opponent_win`.
