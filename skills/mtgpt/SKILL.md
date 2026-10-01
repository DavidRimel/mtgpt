---
name: mtgpt
description: Use when building, auditing, or tuning a Magic the Gathering EDH/Commander deck - resolves every card against Scryfall, checks legality and color identity, measures ratios/curve/colored sources against deckbuilding targets, reports bracket compliance, finds combos, and suggests additions from EDHREC synergy data. Triggers on "EDH", "Commander deck", "decklist", "tune my deck", "deck audit", "is this legal", "what bracket", "combo", "synergy".
---

# mtgpt

Build and tune Commander decks on verified data: thirteen independently
callable operations (`card`, `search`, `classify`, `read`, `validate`,
`audit`, `bracket`, `report`, `synergy`, `themes`, `combos`, `card-combos`,
`suggest`). See `python3 -m mtgpt.cli --help` for the full list.

## The rule that matters

**Never name a card from memory as a recommendation.** Every card you propose
must come back from `mtgpt card`, `mtgpt search`, or `mtgpt synergy` first.
Card names, oracle text, legality, and color identity are all things a model
misremembers confidently, and a decklist with a fake card in it is worse than
no decklist.

The toolkit makes this structural: a name that does not resolve against
Scryfall raises, and the CLI reports it as
`{"ok": false, "error": {"type": "UnresolvedCards", "names": [...]}}` with
exit code 2. If you see that, stop and ask the user — never substitute a
guess.

Run the tooling. Report what it says.

## The envelope

Every operation prints one JSON object:

```
{"ok": true,  "command": "<name>", "data": {...}}
{"ok": false, "command": "<name>", "error": {"type": "...", "message": "...", ...}}
```

Exit code is 0 on success, 2 on user error. Errors are data, not just prose:
`UnresolvedCards` carries `names`; `SourceUnavailable` (EDHREC or Commander
Spellbook down) carries `source`. `suggest` degrades instead of failing — if
EDHREC is unreachable it still returns the gap analysis, with the outage
named in `degraded`.

## Composition loops

Don't run commands in isolation — chain them. The commands below are real
and were run against this toolkit.

### Audit a deck

1. Get the list: ask the user to open Moxfield, click **Export**, and paste
   the text, or point at a saved file.
2. Get the whole picture at once:

   ```bash
   python3 -m mtgpt.cli report --file deck.txt --bracket 3 --text
   ```

   Drop `--text` for the JSON form when you need to reason over the
   structure rather than read it.
3. Or check one stage when only one question matters:
   `validate` for legality only, `audit` for ratios/curve/colored sources
   only, `bracket --target N` for the bracket verdict only.
4. Interpret, don't restate. The user can read numbers; say what they mean
   for how the deck plays. See
   `references/deckbuilding-hygiene.md` before arguing with a number the
   audit reports.

Default to bracket 3 if the user hasn't said what they're aiming at, and say
that you assumed it.

### Tune a deck

1. `python3 -m mtgpt.cli audit --file deck.txt` to find under-served
   categories (status `low` with a `delta`).
2. `python3 -m mtgpt.cli suggest --file deck.txt --bracket 3 --limit 10` for
   ranked candidates, each with a `reason` citing which gap it fills and its
   EDHREC inclusion rate. `suggest` already filters to color identity,
   legality, and the bracket's Game Changer allowance.
3. Vet anything you're considering that `suggest` did not surface:
   `python3 -m mtgpt.cli card "<name>"` or
   `python3 -m mtgpt.cli classify "<name1>" "<name2>"` before naming it.
4. Re-run `audit` (or `report --text`) after a swap to confirm the gap
   actually closed.

### Check a bracket honestly

```bash
python3 -m mtgpt.cli bracket --file deck.txt --target 3
python3 -m mtgpt.cli report  --file deck.txt --bracket 3 --combos
```

Every bracket report carries `deferred_checks`: without `--combos`, it
always includes the two-card infinite combo gap, because that check needs
Commander Spellbook data the plain `bracket`/`report` calls don't fetch.
**Do not claim a deck is bracket-legal while that line is present.** Add
`--combos` (report only; slower — roughly one Spellbook request per distinct
card, about 7 seconds for a 100-card deck) when the combo rule actually
matters, e.g. for brackets 1-3 where two-card infinites are excluded.

### Explore a commander before building

```bash
python3 -m mtgpt.cli themes  "Atraxa, Praetors' Voice"
python3 -m mtgpt.cli synergy "Atraxa, Praetors' Voice" --variant upgraded --limit 20
```

`themes` shows the archetypes this commander is actually built as and the
bracket spread of recorded decks. `synergy` returns Scryfall-verified
candidates with `synergy` score and `inclusion_rate`; `--variant` narrows to
`budget`, `expensive`, `upgraded`, or `cedh` builds.

### Investigate a combo

```bash
python3 -m mtgpt.cli card-combos "Thassa's Oracle"
python3 -m mtgpt.cli combos --file deck.txt
```

`card-combos` lists every known combo using one card. `combos --file` checks
what a specific decklist actually assembles — only combos whose every piece
is present in the deck.

## Reading the numbers, not restating them

- **A category is LOW** — name the specific cards worth adding (via
  `suggest`, or `search`/`card` to vet your own idea), not just "add more
  removal."
- **The curve is HIGH** — look for expensive cards that don't advance the
  commander's plan, rather than cutting the biggest mana values by reflex.
- **Colored sources are SHORT** — this is a mana base problem, not a spell
  problem. Fix the lands before touching the spell list. See
  `references/deckbuilding-hygiene.md` for the source-count table.
- **`tutor_count` triggers a bracket warning** — this is about combo
  assembly speed, not card selection taste. Land fetches are deliberately
  excluded from the tutor count (they're counted as ramp instead).

## Honesty requirements

Every bracket report ends with what it did **not** check
(`deferred_checks`). Surface that list to the user rather than hiding it.
When `suggest` or any EDHREC-backed call returns a non-empty `degraded`
list, say so — a partial answer presented as complete is worse than a
visibly partial one. See `references/brackets.md` for exactly what is and
isn't checked and why.

## What does not exist

Do not improvise these by hand:

- **No Moxfield URL fetching.** Moxfield serves scripted requests a
  Cloudflare challenge; the user must paste the Export text or a saved file.
- **No goldfish simulation.**
- **No deck-from-scratch generation** — `synergy`/`themes` inform a build,
  but nothing assembles a full 99 automatically.
- **Classification is heuristic.** `report --text`'s `CARD TAGS` section
  exists precisely so a wrong tag is visible and correctable, not hidden
  inside a ratio.

## Reference material

Load these only when the question calls for them:

- `references/deckbuilding-hygiene.md` — what each target means and why,
  and the colored-source table. Read this before arguing with a number the
  audit reports.
- `references/brackets.md` — the bracket 1-5 table and exactly what mtgpt
  checks versus defers.
