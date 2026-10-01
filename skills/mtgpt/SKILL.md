---
name: mtgpt
description: Use when building, auditing, or tuning a Magic the Gathering EDH/Commander deck - resolves every card against Scryfall, checks legality and color identity, measures ratios/curve/colored sources against deckbuilding targets, reports bracket compliance, finds combos, suggests additions from EDHREC synergy data, and goldfishes a deck to measure how it plays. Triggers on "EDH", "Commander deck", "decklist", "tune my deck", "deck audit", "is this legal", "what bracket", "combo", "synergy", "goldfish", "simulate", "how does it play".
---

# mtgpt

Build and tune Commander decks on verified data: seventeen independently
callable operations (`card`, `search`, `classify`, `read`, `validate`,
`audit`, `bracket`, `report`, `synergy`, `themes`, `combos`, `card-combos`,
`suggest`, `goldfish`, `goldfish-compare`, `goldfish-new`, `goldfish-step`).
See `python3 -m mtgpt.cli --help` for the full list.

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
   the text, or point at a saved file. A `--file` must be UTF-8; if mtgpt
   reports a `UnicodeDecodeError`, have the user re-save as UTF-8 or pipe the
   text in with `--stdin`.
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

### Goldfish a deck

Goldfishing plays the deck against no opponents and measures four things:
the turns before the commander go to ramp and engines (`setup`), the
commander lands on or before curve (`commander`), the deck does its thing
while holding interaction (`thing`), and how fast it wins (`win`), plus how
it recovers from disruption (`disruption`).

1. **Ask the user what a winning state is for this deck, and what the
   commander's "thing" is. Never infer either.** Map the answer onto an
   archetype — `voltron`, `go_wide`, `aristocrats`, `spellslinger`,
   `combo`, `big_mana`, or `custom` — and write `<deck>.goal.json` next to
   the decklist:

   ```json
   {
     "archetype": "aristocrats",
     "commander_turn": 3,
     "engine": {
       "Blood Artist":  {"on": "creature_dies", "drain": 1},
       "Viscera Seer":  {"sac_outlet": true}
     },
     "win": {"any": [{"opponent_life_lost": 120}, {"cast": "Craterhoof Behemoth"}]},
     "disruption": {"commander_removal": 0.15, "board_wipe": 0.05, "from_turn": 4}
   }
   ```

   The archetype supplies a default `thing` and `win`; override either.
   `combo` and `custom` have no defaults, and `big_mana` needs its finisher
   named. Show the user the file and confirm it before running. The sim only
   models value — mana, draw, tutors, power — so name the deck's engine
   cards under `engine` with what they do (`on` one of `creature_dies`,
   `creature_etb`, `spell_cast`, `instant_sorcery_cast`, `upkeep`, `attack`;
   effects `drain`, `draw`, `treasure`, `tokens`/`token_power`, `anthem` (number: +N
   power to each creature you control); tags `sac_outlet`, `payoff`, `finisher`;
   `priority` `engine` or `hold`). Engine names must be in the deck: a `GoalError`
   names the field and value at fault. An override replaces the card's parsed
   effect, so restate anything from its text you still want (a Phyrexian Arena
   override with only a `drain` no longer draws).
2. **Ask which mode to run, every time; never pick for the user:**
   - **Auto** — 1000 heuristic games; the statistics that drive tuning.

     ```bash
     python3 -m mtgpt.cli goldfish --file deck.txt --goal deck.goal.json
     ```

   - **Pilot** — you play N games (default 3) — `--game 0`, `--game 1`, `--game 2`
     of the same `--seed`, each with its own `--out` file — turn by turn and
     narrate each turn's choices:

     ```bash
     python3 -m mtgpt.cli goldfish-new  --file deck.txt --goal deck.goal.json --seed 1 --game 0 --out game0.json
     python3 -m mtgpt.cli goldfish-step --state game0.json --action '{"play_land": "Forest"}'
     python3 -m mtgpt.cli goldfish-step --state game0.json --action '{"cast": "Sol Ring"}'
     python3 -m mtgpt.cli goldfish-step --state game0.json --action '{"pass": true}'
     ```

     Choose only from the view's `legal_actions`; a tutor waits for
     `{"tutor": "<name>"}`. The view's `mana_available` is mana you can spend
     right now, while the `{"mana_available": N}` condition counts the board's
     per-turn production plus Treasures. **Never open the state file** — it holds the
     library order, which a player would not know. Read the view.
   - **Both** — auto first, then pilot games `--game 0..N-1` of the same
     `--seed`: pilot game I is dealt exactly as auto game I (same shuffle and
     disruption dice), so report each pilot game's checkpoint turns next to the
     auto distribution and say where your line beat or lost to the heuristic's on
     the same deal.
3. Name the weakest block — setup, commander, thing, or win — and say what
   it means for how the deck plays. Surface `notes.unmodeled`: those cards'
   text did nothing in the sim, so offer an `engine` override for any that
   matter. Check `notes.goal_warnings`: each names a `count` in the goal no
   card in the deck can meet, so fix the goal before reading the numbers.
4. Find candidates with `suggest`, `search`, and `classify` — never from
   memory — and swap them into a copy of the list.
5. Re-run in the same mode. For auto, compare on matched seeds:

   ```bash
   python3 -m mtgpt.cli goldfish-compare --file deck.txt --file deck-v2.txt --goal deck.goal.json
   ```

   For pilot, replay the same `--seed` and `--game` numbers against the new list.
6. Report the differences and keep or revert the swap. Later iterations
   reuse the user's mode unless they ask to change it.

Goldfish numbers are optimistic by construction: no opponent, no blockers,
no interaction but the disruption dice. Say so, and use them to compare
versions of a deck, not to predict real games. Instant and sorcery
interaction is held, never cast, by the auto pilot; `thing.interaction_while_online`
and `covered_rate` are how much of it you are holding while the plan is live.
Interaction permanents (Equipment, Lightning Greaves, Mother of Runes) are cast,
and on the battlefield they protect the commander from removal.

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
  The blockage is reversible, so say so if the user asks: fetching needs a
  real browser, and Chromium cannot launch here without four system libraries
  that require root to install (`libnspr4`, `libnss3`, `libnssutil3`,
  `libasound2`) — see the README's "Moxfield URL fetching" section.
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
