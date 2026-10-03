# Goldfishing a deck

### Goldfish a deck

Goldfishing plays the deck against no opponents and measures four things:
the turns before the commander go to ramp and engines (`setup`), the
commander lands on or before curve (`commander`), the deck does its thing
while holding interaction (`thing`), and how fast it wins (`win`), plus how
it recovers from disruption (`disruption`).

0. **Scan the deck before every goldfish. This is not optional.** A goldfish
   is only as good as the sim's model of each card, and a card the sim gets
   wrong silently skews every number.

   ```bash
   python3 -m mtgpt.cli goldfish-scan --file deck.txt [--goal deck.goal.json]
   ```

   For each card the scan shows its oracle text, what the sim does with it
   (`model`, and whether that comes from its `text`, the `library`, or the
   `goal`), and its entry in the card rules library. Review every name in
   `needs_review` — the cards no earlier deck has reviewed — and record a
   verdict for each:

   ```bash
   python3 -m mtgpt.cli card-rule "Smothering Tithe" --status parsed --note "Treasure per opponent draw, 1/round"
   python3 -m mtgpt.cli card-rule "Blood Artist" --status override --rule '{"on": "creature_dies", "drain": 1}' --note "drain 1 each opponent on any death"
   python3 -m mtgpt.cli card-rule "Swords to Plowshares" --status ignored --note "removal; no target in a goldfish"
   ```

   - `parsed` — the model matches what the card does in a goldfish.
   - `override` — the parser misses it but an engine-override rule
     captures it (the fields under **engine** below). The rule then applies to
     every future deck with that card.
   - `ignored` — it only affects opponents (removal, counters, theft); say so.

   If a card matters and neither the parser nor an override can express it,
   the engine needs a new mechanic: tell the user, propose it, and build it
   test-first (as cascade, Approach, and the land searches were) before
   trusting the numbers. Never run a goldfish over unreviewed cards without
   saying which ones were skipped. The library (`mtgpt/data/card_rules.json`)
   accumulates across decks: a card reviewed once stays reviewed. A goal
   file's own `engine` entry overrides the library for that deck.

1. **Ask the user what a winning state is for this deck, and what the
   commander's "thing" is. Never infer either.** Map the answer onto an
   archetype — `voltron`, `go_wide`, `aristocrats`, `spellslinger`,
   `combo`, `big_mana`, or `custom` — and write `<deck>.goal.json` next to
   the decklist (in a project, the goal lives at `decks/<slug>/goal.json`):

   ```json
   {
     "archetype": "aristocrats",
     "commander_turn": 3,
     "engine": {
       "Blood Artist":  {"on": "creature_dies", "drain": 1},
       "Viscera Seer":  {"sac_outlet": true}
     },
     "win": {"any": [{"opponent_life_lost": 120}, {"cast": "Craterhoof Behemoth"}]},
     "disruption": {"commander_removal": 0.15, "board_wipe": 0.05, "from_turn": 4},
     "opponent_win": {"from_turn": 5, "every": [2, 3], "answers": ["removal", "counterspell", "stax"]}
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
   `priority` `engine` or `hold`; `alt_cost`, a mana cost such as
   `"{W}{U}{B}{R}{G}"` that any spell may be cast for while this permanent is
   out — Jodah, Fist of Suns — with commander tax still added; `mana` (number)
   with `mana_colors` (e.g. `"WUBRG"`) for a permanent that taps for more than
   its text parses — Bloom Tender, Faeburrow Elder, or Lotus Cobra's landfall as
   a per-turn estimate). Engine names must be in the deck: a `GoalError`
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
     `{"tutor": "<name>"}`, and Enter the Infinite for `{"put_back": "<name>"}`
     (the card put on top of your library). The view's `mana_available` is mana you can spend
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

For a fast table (bracket 4 and up) add `opponent_win`: from that round on an
opponent tries to win each round, and you lose unless you hold removal or a
counterspell (spent) or have a stax piece out. Read `opponent_win` and
`win.win_by_round` in the report.

Drawing from an empty library loses: the report's `loss` block gives the loss
rate, the rounds, and the reason (`decked`). Enter the Infinite draws the whole
library; the auto pilot casts it only when its look-ahead — playing out the rest
of the round, extra turns included, with the mana left — ends in a win, and puts
back a Nexus of Fate if it holds one. Give Omniscience `"alt_cost": "{0}"` so its
free casting is modeled.

Extra turns are full turns (untap, draw, land drop, main, combat) that keep the
table-turn number: every turn count in the report is a full round of the table,
so a win on an extra turn is credited to the round its spell was cast in. No
disruption is rolled on an extra turn. In pilot mode the view's `extra_turn` says
when you are in one.

Goldfish numbers are optimistic by construction: no opponent, no blockers,
no interaction but the disruption dice. Say so, and use them to compare
versions of a deck, not to predict real games. Instant and sorcery
interaction is held, never cast, by the auto pilot; `thing.interaction_while_online`
and `covered_rate` are how much of it you are holding while the plan is live.
Interaction permanents (Equipment, Lightning Greaves, Mother of Runes) are cast,
and on the battlefield they protect the commander from removal.

## Target round

A goal may set `"target_round"` (a whole number of rounds). The scorecard's
primary metric is wins by that round; without it the bracket default applies:
bracket 1–2 → 7, bracket 3 → 5, bracket 4 → 4, bracket 5 → 3.
