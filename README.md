# mtgpt

Build and tune Magic: The Gathering Commander decks with Claude, on data that
has been verified rather than remembered.

The problem it solves: asked to tune a deck, a language model will invent
cards, misquote oracle text, break color identity, and reason about mana
bases by feel. mtgpt splits the work — scripts establish facts, the model
makes judgments. Every card in a report has been resolved against Scryfall,
so a fake card cannot reach you.

## Install

Unzip the repo (or clone it), open a terminal in its folder, and run `claude`.
The `mtgpt` skill loads automatically: ask it to list your decks, start a new
one, or tune one. Requires Python 3.12+ (the skill checks on first run).

Decks live in `decks/`, which git ignores — they are your save files. To share
the tool, run `scripts/make-zip.sh`; the zip holds only committed files.

## Use directly

mtgpt is twenty-six independently callable operations, each emitting one
JSON object: `card`, `search`, `find`, `cross-check`, `classify`, `import`, `read`, `validate`,
`audit`, `bracket`, `report`, `compare`, `synergy`, `themes`, `combos`, `card-combos`,
`suggest`, `goldfish`, `goldfish-compare`, `goldfish-new`, `goldfish-step`,
`goldfish-scan`, `card-rule`, `scorecard`, `project`, `card-rule-merge`.

```bash
# Full picture, human-readable
python3 -m mtgpt.cli report --file mydeck.txt --bracket 3 --text

# Same thing, as structured JSON
python3 -m mtgpt.cli report --file mydeck.txt --bracket 3

# One card, verified
python3 -m mtgpt.cli card "Sol Ring"

# Ranked suggestions for what to add
python3 -m mtgpt.cli suggest --file mydeck.txt --bracket 3

# Candidates and how a commander is usually built
python3 -m mtgpt.cli synergy "Atraxa, Praetors' Voice" --variant upgraded
python3 -m mtgpt.cli themes  "Atraxa, Praetors' Voice"

# Import from an Archidekt URL, or pass --url to any deck operation
python3 -m mtgpt.cli import  "https://archidekt.com/decks/2000000/my-deck"
python3 -m mtgpt.cli bracket --url "https://archidekt.com/decks/2000000/" --target 3

# Cards that do a job, by community-curated function tag
python3 -m mtgpt.cli find ramp --identity wubg --limit 10

# What the typical build of this commander plays that yours does not
python3 -m mtgpt.cli compare --file mydeck.txt

# Combo detection
python3 -m mtgpt.cli card-combos "Thassa's Oracle"
python3 -m mtgpt.cli combos --file mydeck.txt

# Goldfish: how the deck plays, against a goal file you write per deck
python3 -m mtgpt.cli goldfish         --file mydeck.txt --goal mydeck.goal.json
python3 -m mtgpt.cli goldfish-compare --file mydeck.txt --file mydeck-v2.txt --goal mydeck.goal.json
```

Every subcommand that takes a decklist accepts `--file <path>` or `--stdin`:

```bash
pbpaste | python3 -m mtgpt.cli audit --stdin
```

Output is `{"ok": true, "command": ..., "data": {...}}` on success or
`{"ok": false, "command": ..., "error": {"type": ..., "message": ...}}` on
failure, with exit code 0 or 2 respectively. See
`.claude/skills/mtgpt/references/toolkit.md` for the full composition patterns.

Archidekt links can be fetched directly: `import`, or `--url` on any deck
operation. Export your list from Moxfield with the **Export** button and paste
it into a file — Moxfield serves scripted requests a Cloudflare challenge, so
automated fetching is not available there. See
[Moxfield URL fetching](#moxfield-url-fetching) for what it would take.

Decklist files must be UTF-8. A Windows editor's "ANSI" (cp1252) or
PowerShell 5's `… > deck.txt` (UTF-16) will be rejected with an explanatory
error rather than parsed into mangled card names; re-save as UTF-8, or pipe the
text in with `--stdin`.

## What it reports

- **Legality** — 100 cards, singleton, commander legality, color identity, ban list
- **Composition** — lands, ramp, draw, removal, sweepers, protection against target bands
- **Curve** — average mana value and a mana value histogram
- **Colored sources** — whether the mana base supports each color's heaviest card
- **Bracket** — compliance with brackets 1-5, and what it could not check
- **Synergy and themes** — EDHREC candidates and inclusion rates for a commander, and how it's usually built
- **Combos** — Commander Spellbook combos for one card, or what a decklist actually assembles
- **Suggestions** — ranked cards to add, each justified by the gap it fills and (when available) its EDHREC inclusion rate
- **Function search** — cards carrying a community-curated Scryfall Tagger `otag:`, scoped to a color identity, each cross-checked against mtgpt's own classification so a disagreement is visible
- **Average-deck diff** — overlap with EDHREC's consensus build of the commander, what it plays that yours does not (tagged by function), and what is unique to yours
- **Goldfish** — over many simulated games: whether early turns ramp, whether the commander lands on curve, how often the deck's plan is online with interaction in hand, how it recovers from removal and wipes, and how fast it wins

## Status

Layers 1-3. Working now: ingest, Archidekt URL import, Scryfall resolution,
validation, function classification, Scryfall Tagger function search, the ratio
and curve audit, bracket checks, EDHREC synergy, themes and average-deck diff,
Commander Spellbook combo detection, gap-driven suggestions, and goldfish
simulation (auto, and Claude-piloted turn by turn).

Not built yet, and deliberately not improvised by the skill: Moxfield URL
fetching (see below), and building a full deck from scratch given only a
commander. `find` cannot cover mass land denial — no Scryfall Tagger tag for
it resolves.

### Moxfield URL fetching

Not available. Moxfield serves scripted requests a Cloudflare challenge, so
automated fetching needs a real browser — and Chromium cannot launch in this
environment without system libraries that require root to install
(`libnspr4`, `libnss3`, `libnssutil3`, `libasound2`). Verified 2026-10-01.

Use Moxfield's **Export** button and pass the text to `--file` or `--stdin`.
Every deck operation accepts both, plus `--url` for an Archidekt link.

## Data sources

| Source | Use |
|---|---|
| [Scryfall](https://scryfall.com) | Card data, legality, color identity, Game Changers |
| [EDHREC](https://edhrec.com) | Synergy, inclusion rates, and archetype themes |
| [Commander Spellbook](https://commanderspellbook.com) | Combo detection |

## Development

```bash
python3 -m pytest
```

Tests never touch the network; they run against fixtures in `tests/fixtures/`.

## Design docs

- [Design](docs/superpowers/specs/2026-09-30-mtgpt-design.md)
- [Layer 1 plan](docs/superpowers/plans/2026-09-30-mtgpt-layer1.md)
- [Goldfish design](docs/superpowers/specs/2026-10-01-goldfish-design.md)
- [Goldfish plan](docs/superpowers/plans/2026-10-01-goldfish.md)
