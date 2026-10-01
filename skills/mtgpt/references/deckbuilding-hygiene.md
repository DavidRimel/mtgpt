# Deckbuilding hygiene

The values `mtgpt audit` and `mtgpt report` check live in `mtgpt/targets.py`.
This file explains what each one is for. When a number here and a number
there disagree, `targets.py` is correct and this file needs updating.

These are starting points for a typical midrange Commander deck, not laws. A
deck that misses a target for a reason it can articulate is fine. A deck that
misses one because nobody counted is the problem this tool solves.

## Lands: 36-38

The most common failure in a homebrew deck is too few lands. 36 is the floor
for a deck with an average mana value near 3; go to 38 when the curve is
higher or ramp is thin. You may go below 36 only when the deck is genuinely
cheap and carries plenty of ramp.

MDFC land backs are reported separately (`mdfc_land_count`) and counted
toward `mana_sources`, not toward `land_count`. A card whose front you want
to cast is not a land on the turn you need it to be one.

## Ramp: 10-12

Mana rocks, mana dorks, and land-fetch spells. Land fetches count here, not
as tutors — `classify` tags a land-fetch pattern as `ramp`, and the tutor
regex in `mtgpt/classify.py` explicitly excludes it.

### Count the basics before trusting the ramp count

A land-fetch spell is only as good as the lands it can find, and the audit
cannot tell you that: it counts Cultivate as `ramp` whether the deck has
twenty basics or none. **Before recommending or keeping a land-fetch card,
count the deck's basic lands and read what the card actually searches for.**

- **"basic land card"** — Cultivate, Kodama's Reach, Rampant Growth,
  Sakura-Tribe Elder, Solemn Simulacrum, Burnished Hart. With no basics these
  whiff completely; with only a few, they thin the basics out early and go
  dead later.
- **a land *type*** ("Forest card", "Plains, Island, Swamp, or Mountain card")
  — Nature's Lore, Three Visits, Farseek, Skyshroud Claim, Wood Elves, every
  fetch land. These find any land with that type, so they grab original duals,
  shocks, and triomes. Count how many lands carry the type before relying on
  one.
- **"land card(s)"** — Reshape the Earth, Crop Rotation. Anything goes.
- **No search at all** — mana dorks, rocks, Treasure makers (Tireless
  Provisioner), extra land drops (Dryad of the Ilysian Grove, Azusa, Oracle of
  Mul Daya). Unaffected by the manabase.

A greedy manabase of duals, shocks, and triomes with zero basics should run
type-searching ramp and mana creatures and rocks, never basic-only fetches.
When auditing such a deck, flag every basic-only fetch as dead, even if the
audit's ramp band reads `ok`.

## Card draw: 8-12

Commander games go long, and card advantage is what converts a good board
into a win. Repeatable draw engines are worth more than one-shot refills, so
a deck at 8 with three engines is healthier than one at 12 with none.

## Spot removal: 5-8

Targeted answers. Below 5, the table's best threat resolves and stays.
Instant-speed and unconditional answers are worth more than sorcery-speed
conditional ones.

## Sweepers: 2-3

Board wipes. Go to the low end when the deck itself goes wide, since a
symmetrical wipe hurts a token deck more than it helps.

## Protection: 3-5

Ways to keep the commander or the board alive. Weight this up when the deck
cannot function without its commander on the battlefield.

## Total mana sources: 46-50

Lands plus ramp plus MDFC land backs (`mana_sources` in the audit output).
This is the number that actually predicts whether the deck functions, and it
is the one to check first when a deck feels clunky.

## Average mana value: 2.8-3.2

Computed across the nonland cards (`average_mana_value`, with `curve_status`
of `low`/`ok`/`high`). Above the band, the deck needs more lands and more
ramp than the defaults. Below it, the deck can afford to cut a land.

## Colored sources

A card with three pips of one color needs far more sources of that color than
a card with one. The thresholds in `targets.py` (`PIP_SOURCE_MINIMUMS`) are
adapted from Frank Karsten's source-count methodology for 100-card singleton
decks:

| Pips of a color in one card | Sources of that color wanted |
|---|---|
| 1 | 14 |
| 2 | 20 |
| 3 | 26 |

The audit sizes each color against the most demanding single card in that
color (`max_pips` in the `pips` section of the audit output) — the honest
test: a deck that cannot reliably cast its own triple-pip card has a mana
base problem regardless of how the totals look.

Hybrid pips count toward both colors, which overstates demand slightly. That
is the safe direction to be wrong in.
