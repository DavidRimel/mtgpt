"""Every threshold the audit checks, in one place.

The prose rationale lives in skills/mtgpt/references/deckbuilding-hygiene.md.
That file explains the numbers; this file defines them. Keeping values in code
and reasoning in markdown is what stops the two from drifting apart.

All bands are inclusive (min, max) tuples.
"""

from __future__ import annotations

from .models import Function

#: Lands in the 99. Flexes down with a low curve and heavy ramp.
LAND = (36, 38)

#: Mana rocks, dorks, and land-fetch spells.
RAMP = (10, 12)

#: Repeatable and burst card advantage.
DRAW = (8, 12)

#: Targeted answers to a single threat.
SPOT_REMOVAL = (5, 8)

#: Board wipes. Fewer if the deck itself goes wide.
SWEEPER = (2, 3)

#: Ways to protect the commander or the board.
PROTECTION = (3, 5)

#: Lands plus ramp plus MDFC land backs.
MANA_SOURCES = (46, 50)

#: Acceptable average mana value across the nonland cards.
AVERAGE_MV_BAND = (2.8, 3.2)

#: Colored sources needed to cast a card with N pips of a color on curve.
#: Adapted from Frank Karsten's source-count methodology for 100-card
#: singleton decks. Heuristic, and tunable here.
PIP_SOURCE_MINIMUMS = {1: 14, 2: 20, 3: 26}

#: Functions the audit reports a target band for, in report order.
CATEGORY_TARGETS: dict[Function, tuple[int, int]] = {
    Function.LAND: LAND,
    Function.RAMP: RAMP,
    Function.DRAW: DRAW,
    Function.SPOT_REMOVAL: SPOT_REMOVAL,
    Function.SWEEPER: SWEEPER,
    Function.PROTECTION: PROTECTION,
}
