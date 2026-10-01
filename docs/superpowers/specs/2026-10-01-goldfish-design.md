# mtgpt Goldfish — Design

**Date:** 2026-10-01
**Status:** Draft, pending user review
**Scope:** Layer 3 of [the mtgpt design](2026-09-30-mtgpt-design.md): a goldfish simulator
that measures how a Commander deck plays, so it can be tuned in a measure → change →
re-measure loop.

## Problem

The audit counts cards. It cannot say whether those cards come together: whether the
early turns actually ramp, whether the commander lands on curve, whether the deck does
its thing while holding interaction, or how fast it wins. Those are questions about
*play*, and they are what a pilot actually feels.

## What the user asked for

Measured over many goldfished games:

1. **Setup** — turns before the commander go to ramp and value engines.
2. **Commander** — cast on or before curve.
3. **The thing** — how well the deck does its commander's plan while holding enough
   interaction (protection, removal) to defend it and answer threats.
4. **Win** — how fast the deck reaches a winning state.

Plus, added during design:

5. **Disruption** — random commander removal and board wipes, to measure how protection
   gets used and how fast the deck recovers.
6. **Pilot mode** — Claude can play individual games turn by turn, as a proxy for how a
   human would pilot the deck.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Fidelity | Goldfish only. No opponents. | Win and interaction are approximated: a win is a threshold state; interaction is measured as what is held in hand while the plan is online. |
| Who defines "the thing" and "win" | The user, per deck. The skill asks before simulating. | Code cannot infer a commander's plan; Claude must not guess it either. |
| How the definition is expressed | An archetype template plus per-deck overrides, in a goal file. | Templates keep the scoring stable across runs; overrides carry what is specific to the deck. |
| Card behavior | Generic effects from oracle text plus per-deck engine overrides. | Only value-generating text matters: mana, draw, tutors. Everything else is ignored and reported as ignored. |
| Who plays | One engine, two policies: a fixed heuristic (auto) and Claude (pilot). | Auto gives sample sizes that can detect a one-card swap; pilot gives realistic play and a check on the heuristic. |

## Non-goals

- Opponents, combat against blockers, the stack, or responding to anything.
- Executing arbitrary card text. Only the effects in [Card effects](#card-effects) exist.
- Inferring the win condition or the commander's plan automatically.
- A Forge or other rules-engine backend.

## Architecture

| Module | Responsibility | Depends on |
|---|---|---|
| `mtgpt/effects.py` | `effect_of(card) -> SimEffect`: what a card does in the sim. Pure. | `models`, `classify` regexes |
| `mtgpt/goal.py` | Load and validate a goal file against a resolved deck; expand archetype templates. Pure. | `models` |
| `mtgpt/goldfish/engine.py` | `GameState`, legal actions, applying an action, turn structure, disruption, checkpoint evaluation. Pure, seeded, JSON-serializable state. | `effects`, `goal` |
| `mtgpt/goldfish/policy.py` | The heuristic policy: `choose(state) -> Action`. | `engine` |
| `mtgpt/goldfish/run.py` | Plays N auto games, aggregates a `GoldfishReport`; `compare` of two decks on matched seeds. | `engine`, `policy` |
| `mtgpt/api.py`, `cli.py` | `goldfish`, `goldfish-compare`, `goldfish-new`, `goldfish-step` operations. | above |
| `mtgpt/models.py` | `Card` gains `power` and `toughness` (`float \| None`, defaulted, last fields). `card_from_json` fills them from the front face. | — |

The engine never touches the network. The deck arrives already resolved, through the
same `resolve` path every other operation uses.

### Statelessness

The toolkit's operations are stateless, and pilot mode keeps that: the full `GameState`,
including the RNG state, is serialized to JSON. `goldfish-step` reads a state, applies
one action, and writes the new state. No call depends on hidden process state.

## Card effects

`effect_of(card)` returns a `SimEffect` with these fields, all optional:

| Field | Parsed from | Example |
|---|---|---|
| `mana` (permanent, per untap) and `mana_colors` | `{T}: Add …` on a nonland permanent | Sol Ring = 2, Llanowar Elves = 1 G |
| `mana_once` | Rituals, Treasure creation | Dark Ritual |
| `fetch_lands` (count, to battlefield or hand, tapped or not) | Land-search text | Cultivate = 1 to battlefield tapped + 1 to hand |
| `draw_once` | "draw N cards" on a spell or ETB | Night's Whisper = 2 |
| `draw_per_turn` | Upkeep or "at the beginning of" draw on a permanent | Phyrexian Arena = 1 |
| `tutor` (destination and restriction: any, creature, artifact, enchantment, instant/sorcery) | Nonland "search your library" | Demonic Tutor = any, to hand |
| `power` | `Card.power` on creatures | — |
| `power_bonus` | `+N/+N` on an Equipment or Aura | Bonesplitter = 2 |
| `held` (removal / sweeper / protection / counterspell) | Existing `classify` tags | Swords to Plowshares = removal |

Lands produce their `produced_mana`; enters-tapped is read from oracle text. An MDFC
with a land back may be played as that land.

A card whose text yields no effect is still castable (it costs mana and, if a creature,
adds power). Its name is listed in `notes.unmodeled` so the user can see what the sim
ignored and add an override if that card matters.

## Goal file

Written by Claude after asking the user what a winning state looks like, and saved next
to the decklist as `<deck>.goal.json`.

```json
{
  "archetype": "aristocrats",
  "commander_turn": 3,
  "engine": {
    "Blood Artist":       {"on": "creature_dies", "drain": 1},
    "Viscera Seer":       {"sac_outlet": true},
    "Pitiless Plunderer": {"on": "creature_dies", "treasure": 1}
  },
  "thing": {"all": ["commander", {"count": "sac_outlet", "min": 1}, {"count": "drain", "min": 1}]},
  "win":   {"any": [{"opponent_life_lost": 120}, {"cast": "Craterhoof Behemoth"}]},
  "disruption": {"commander_removal": 0.15, "board_wipe": 0.05, "from_turn": 4}
}
```

### Archetypes

An archetype supplies a default `thing` and `win`; the goal file overrides either.

| Archetype | Default `thing` | Default `win` |
|---|---|---|
| `voltron` | Commander on board with ≥ 2 equipment/auras | `commander_damage` ≥ 63 (21 × 3) |
| `go_wide` | ≥ 5 creatures on board | `opponent_life_lost` ≥ 120 |
| `aristocrats` | Commander + 1 sac outlet + 1 drain piece | `opponent_life_lost` ≥ 120 |
| `spellslinger` | Commander + ≥ 1 per-spell payoff | `opponent_life_lost` ≥ 120 |
| `combo` | None by default; must be given | `assembled` the named pieces |
| `big_mana` | ≥ 10 mana available | `cast` a finisher; must be named |
| `custom` | None; must be given | None; must be given |

### Condition vocabulary

Conditions combine with `all` and `any`, nested to any depth.

- `"commander"` — a commander is on the battlefield.
- `{"count": <engine tag or function>, "min": N}` — permanents on board carrying that
  tag (`sac_outlet`, `drain`, `payoff`, a `classify` function, or `creature`).
- `{"mana_available": N}`, `{"board_power": N}`, `{"cards_in_hand": N}`.
- `{"opponent_life_lost": N}` — cumulative, total across all opponents (120 = 40 × 3).
- `{"commander_damage": N}` — cumulative commander combat damage.
- `{"cast": "<name>"}` — that card has resolved this game.
- `{"assembled": ["<name>", …]}` — all named cards on battlefield or in hand.

### Engine overrides

Keyed by card name; each name must be in the resolved deck. Fields:

- Triggers: `"on"` ∈ `creature_dies`, `creature_etb`, `spell_cast`, `upkeep`, `attack`,
  with any of `drain` (life lost per opponent), `draw`, `treasure`, `tokens`
  (count and power).
- Static tags: `sac_outlet`, `payoff`, `finisher`, `anthem` (power bonus per creature).
- `priority`: `"engine"` (cast right after the commander) or `"hold"` (never cast by
  the auto policy until the `win` condition becomes reachable this turn).
- `alt_cost`: a mana cost any spell may be paid with instead of its own while this
  permanent is on the battlefield (Jodah, Archmage Eternal; Fist of Suns). The engine
  pays whichever payable cost is cheaper; commander tax adds to either.
- `mana` with `mana_colors`: mana the permanent taps for each turn, for sources whose text
  the parser cannot count (Bloom Tender, Faeburrow Elder; Lotus Cobra as an estimate).

An override replaces the parsed effect for that card: only its power, `power_bonus`, and
land face survive, so restate anything from its text the sim should still do.

### Combat

There are no blockers, so each turn from the turn after a creature arrives, all creatures
attack. Equipment and Auras are treated as attached to the commander while it is on the
battlefield, adding their `power_bonus` to it; equip costs are ignored. Combat damage
counts once toward `opponent_life_lost` (it is dealt to one opponent), while `drain`
counts once per opponent (3×). The commander's combat damage also adds to
`commander_damage`. Sac outlets sacrifice token creatures after combat when an
`on: creature_dies` payoff is on board.

## The engine and a turn

Game setup: shuffle with the game's seed; commander(s) in the command zone.

**Mulligan.** London mulligan. The first mulligan is free. Keep 3-5 lands, or 2 lands plus
a ramp spell of mana value 2 or less. Otherwise mulligan, to a floor of 5 cards. The
mulligan is an engine rule, the same in auto and pilot mode.

**Turn**, until the `win` condition holds or the turn cap (default 10) is reached:

1. Untap. Upkeep triggers. Draw (no draw on turn 1).
2. Disruption roll, from `from_turn` on (see below).
3. Main phase: the policy chooses actions until it passes.
4. Combat.
5. End: evaluate checkpoints; record the first turn each holds.

**Actions**, as JSON: `{"play_land": name}`, `{"cast": name}`, `{"tutor": name}` (the only
choices while a tutor resolves), `{"sacrifice": name}` (with a sac outlet out), and
`{"pass": true}`, which ends the turn. The engine only offers legal actions: correct mana and
colors, one land per turn, commander tax applied. Anything else raises `IllegalAction`, which
carries the legal list.

### Heuristic policy (auto mode)

1. Play a land: an untapped one if it lets you cast something this turn, otherwise a tapped
   one; an MDFC land only when no other land is in hand.
2. Cast by priority, cheapest first within a tier, repeating while mana remains:
   1. Ramp.
   2. The commander.
   3. Engine pieces (`priority: "engine"`, or anything the `thing` or `win` condition
      names, interaction included). In a voltron deck, Equipment and Auras that are
      interaction (Lightning Greaves, Swords) are cast here too.
   4. Draw and value.
   5. Other creatures and permanents, including other interaction permanents
      (Mother of Runes, Ravenous Chupacabra).
   6. Instant and sorcery removal, sweepers, protection, counterspells: **never cast**.
      They are held and counted, and protection is spent by disruption. Interaction
      permanents still count as held while in hand. An MDFC whose spell face is
      unmodeled is kept as a land unless the goal names it.
3. Tutor target: the first missing piece of `thing`, then of `win`, then a land if
   under 4 lands, otherwise the highest-priority card not in hand.

### Card mechanics beyond mana and draw

Parsed from card text: land searches by type (a Forest search finds a Bayou);
alternative costs (Jodah, Fist of Suns, Leyline of Mutation; Omniscience's `{0}`
from hand) and Leyline openings; nonland permanents that enter tapped; mana that
can't pay generic costs (Jegantha); multi-card tutors (Conflux); Chromatic Lantern,
Dryad and Orrery fixing; extra land drops and lands off the top; landfall mana and
Treasure; mana per color among your permanents (Bloom Tender); cascade, and Imoti's
grant to 6+ mana-value spells; Approach of the Second Sun's real rule (the second
cast from hand wins, reported as `won:<card>` whatever the goal says); Genesis
Ultimatum and Dig Through Time with delve; One with the Multiverse's free spell,
saved for 5+ mana spells; Emergent Ultimatum (the opponents shuffle back the best
card); Sylvan Library as one extra card a turn; Sterling Grove putting a missing
goal enchantment on top; Consecrated Sphinx drawing 2 per opponent draw while 15+
cards remain; Smothering Tithe as one Treasure a round.

### Card rules library

`mtgpt/data/card_rules.json` records every reviewed card once: `parsed` (the parser
models it), `override` (an engine-override rule, validated like a goal file's), or
`ignored` (opponent-only effects), each with a note. `goldfish-scan` shows every
card's model and library entry and lists those needing review; `card-rule` records
a verdict, checking the name against Scryfall. Library overrides apply to any deck
whose goal file does not override that card: goal file, then library, then text.

### Decking and drawing the library

Drawing from an empty library loses the game on that round; the report's `loss`
block carries the loss rate, loss rounds, and reason. A card that draws cards equal
to the library (Enter the Infinite) draws it all, then waits for `put_back` choices
— cards from hand put on top — before anything else happens. The auto pilot holds
such a card until a look-ahead, playing out the rest of the round with the policy,
ends in a win; it puts back a self-shuffling extra-turn spell first, then a land.

### Extra turns

An extra-turn spell ("take an extra turn", Time Stretch's two) queues full turns —
untap, upkeep, draw, a land drop, main, combat, end — taken right after the current
one. They keep the table-turn number: `turn`, checkpoints, the turn cap, and every
reported turn count full rounds of the table, so a win in an extra turn is credited
to the round it was cast in. Summoning sickness counts every turn taken. No
disruption is rolled on an extra turn, since no opponent has played. A spell that
shuffles itself into its owner's library (Nexus of Fate, Beacon of Tomorrows) goes
there instead of the graveyard. At most 20 extra turns are taken per table turn.

### Disruption

From `from_turn`, each turn rolls independently:

- **Commander removal** (probability `commander_removal`): if a protection permanent is
  on the battlefield, the event is stopped and the permanent stays. Otherwise, if a
  protection card or a counterspell is in hand, it is discarded and the event is stopped. Otherwise the commander
  returns to the command zone and its tax rises by 2.
- **Board wipe** (probability `board_wipe`): stopped the same way, but only by protection
  that survives a wipe (indestructible or phasing) or a counterspell in hand; hexproof, and
  protection on the battlefield, do not stop a wipe. Otherwise every nonland permanent except noncreature mana rocks leaves; lands and
  rocks stay, and creatures that die fire `creature_dies`.

Both dice are rolled every eligible turn whether or not the event could land, so two decks
played on the same seed see the same rolls.

Disruption defaults to off when the goal file has no `disruption` block.

## Pilot mode

The same engine, with Claude choosing actions instead of the heuristic.

```bash
python3 -m mtgpt.cli goldfish-new  --file deck.txt --goal deck.goal.json --seed 7 --out game.json
python3 -m mtgpt.cli goldfish-step --state game.json --action '{"cast": "Sol Ring"}'
```

`goldfish-new` writes the state to `--out`; `goldfish-step` rewrites `--state` in place, and
leaves it untouched when the action is refused. Both print a view: hand, battlefield,
command zone and tax, mana available, legal actions, checkpoints hit so far, and the last
lines of the game log. The view omits the library order, which a player would not know. Each action is checked
by the engine, so Claude can only make legal plays. Effects are the same as auto mode;
Claude decides better (sequencing, tutor targets, when to cast the commander), but reads
no card text the engine would not.

Pilot mode runs only when the user chooses it (see [Skill integration](#skill-integration)),
for a few seeded games (default 3). When both modes run, each pilot game's checkpoint turns
are reported next to the auto distribution, which shows where the heuristic misplays.

## Report

`goldfish --file deck.txt --goal deck.goal.json [--games 1000] [--turns 10] [--seed 1]
[--no-disruption]` returns:

```
setup:      mana_by_turn (mean per turn), pre_commander_mana_spent_on {ramp, engine,
            other, unspent} (%), lands_by_turn, mulligan_rate, stalled_rate
            (≤ 2 lands and no ramp on turn 3)
commander:  target_turn, on_curve_rate (cast by commander_turn), cast_rate,
            cast_turn {p25, median, p75, histogram},
            late_reasons {land_light, no_mana, color_screw, spent_elsewhere}
thing:      online_rate, online_turn {p25, median, p75, histogram},
            interaction_while_online {mean removal, protection, counterspell in hand},
            covered_rate (turns online with ≥ 1 protection AND ≥ 1 removal in hand)
disruption: events, landed, stopped_by_protection_rate (protection or counterspell),
            recovery_turns {median} (turns from event until the thing is online
            again; 0 means back by the end of the same turn), never_recovered,
            win_rate_after_event
win:        win_rate (by turn cap), win_turn {p25, median, p75, histogram},
            by_condition {condition: rate}
notes:      unmodeled (card names, minus those with an engine override), goal_warnings (counts
            no card in the deck can meet), goldfish caveat
```

The report's top level also carries `games`, `seed`, `turn_cap`, `disruption_enabled`, and,
from the API, `warnings` (validation violations).

`goldfish-compare --file old.txt --file new.txt --goal deck.goal.json` runs both decks on
the same seeds and returns each metric's before, after, and difference.

## Skill integration

`SKILL.md` gains a **Goldfish a deck** loop:

1. **Ask the user what a winning state is** for this deck, and what the commander's
   "thing" is. Never infer it. Map the answer onto an archetype; confirm the goal file
   with the user before running.
2. **Ask the user which mode to run**, every time; never pick for them:
   - **Auto** — `goldfish`, 1000 heuristic games. Statistics; drives the tuning loop.
   - **Pilot** — Claude plays N seeded games (default 3, user may change) turn by turn and
     narrates each turn's choices.
   - **Both** — auto first, then pilot games on seeds from the same run, reported side by
     side with the auto distribution.
3. Run the chosen mode.
4. Read the weakest block (setup, commander, thing, win) and say which it is.
5. Find candidates with `suggest`, `search`, and `classify`; never from memory.
6. Swap in a copy of the list and re-run in the same mode: `goldfish-compare` for auto,
   the same seeds for pilot.
7. Report the differences, and keep or revert the swap. Later iterations may reuse the
   user's mode choice unless they ask to change it.

## Error handling

- **Hard stop:** goal file malformed, unknown archetype or condition, an engine name not
  in the deck, `combo` or `custom` without the conditions they require, an illegal pilot
  action. Each error names the offending field or value, as `UnresolvedCards` does.
- **Never an error:** card text the sim cannot model. It goes to `notes.unmodeled`.
- A deck that fails `validate` is still simulated, with the violations returned under
  `warnings`; goldfishing a draft list is legitimate.

## Testing

Offline, against fixtures, matching the existing suite.

- `effects.py` — rocks, dorks, rituals, Treasure, Cultivate-style fetches, draw-N, upkeep
  draw, restricted and unrestricted tutors, MDFC lands, enters-tapped lands, unmodeled
  cards.
- `goal.py` — each archetype's expansion; every validation error; an engine name not in
  the deck.
- `engine.py` — small hand-built decks with known answers: 99 Islands plus Sol Ring puts a
  rock out on turn 1 whenever it is drawn; commander tax after removal; the land-per-turn
  limit; illegal actions are refused; state round-trips through JSON unchanged.
- `policy.py` — priority order; protection and removal are never cast; tutor targeting.
- `run.py` — a fixed seed gives identical reports; disruption at 0 never fires and at 1
  fires every eligible turn; `compare` on identical decks returns all-zero differences.

## Risks

1. **The heuristic misplays some decks.** Mitigated by pilot mode, which shows where, and
   by `priority` overrides in the goal file.
2. **Effect parsing is heuristic**, like classification. Mitigated by `notes.unmodeled`
   and by engine overrides for the cards that matter.
3. **Goldfish flatters a deck.** No opponent means no pressure; win turns are optimistic
   by construction. The report states it is a goldfish; the metrics are for comparing
   versions of a deck, not for predicting real games.
