# mtgpt

Build and tune Magic: The Gathering Commander decks with Claude, on data that
has been verified rather than remembered.

The problem it solves: asked to tune a deck, a language model will invent
cards, misquote oracle text, break color identity, and reason about mana
bases by feel. mtgpt splits the work — scripts establish facts, the model
makes judgments. Every card in a report has been resolved against Scryfall,
so a fake card cannot reach you.

## Install

```bash
/plugin marketplace add DavidRimel/mtgpt
```

Then ask Claude to audit, tune, or explore a Commander deck.

## Use directly

mtgpt is thirteen independently callable operations, each emitting one JSON
object: `card`, `search`, `classify`, `read`, `validate`, `audit`, `bracket`,
`report`, `synergy`, `themes`, `combos`, `card-combos`, `suggest`.

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

# Combo detection
python3 -m mtgpt.cli card-combos "Thassa's Oracle"
python3 -m mtgpt.cli combos --file mydeck.txt
```

Every subcommand that takes a decklist accepts `--file <path>` or `--stdin`:

```bash
pbpaste | python3 -m mtgpt.cli audit --stdin
```

Output is `{"ok": true, "command": ..., "data": {...}}` on success or
`{"ok": false, "command": ..., "error": {"type": ..., "message": ...}}` on
failure, with exit code 0 or 2 respectively. See
`skills/mtgpt/SKILL.md` for the full composition patterns.

Export your list from Moxfield with the **Export** button and paste it into a
file. Moxfield serves scripted requests a Cloudflare challenge, so automated
fetching is not available.

## What it reports

- **Legality** — 100 cards, singleton, commander legality, color identity, ban list
- **Composition** — lands, ramp, draw, removal, sweepers, protection against target bands
- **Curve** — average mana value and a mana value histogram
- **Colored sources** — whether the mana base supports each color's heaviest card
- **Bracket** — compliance with brackets 1-5, and what it could not check
- **Synergy and themes** — EDHREC candidates and inclusion rates for a commander, and how it's usually built
- **Combos** — Commander Spellbook combos for one card, or what a decklist actually assembles
- **Suggestions** — ranked cards to add, each justified by the gap it fills and (when available) its EDHREC inclusion rate

## Status

Layer 1 (and the combo/synergy/suggest operations added after it). Working
now: ingest, Scryfall resolution, validation, function classification, the
ratio and curve audit, bracket checks, EDHREC synergy and themes, Commander
Spellbook combo detection, and gap-driven suggestions.

Not built yet, and deliberately not improvised by the skill: Moxfield URL
fetching (see the note above), goldfish simulation, and building a full deck
from scratch given only a commander.

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
