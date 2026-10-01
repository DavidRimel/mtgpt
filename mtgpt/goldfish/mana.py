# mtgpt/goldfish/mana.py
"""Mana costs, and paying them from what is on the battlefield.

Paying is a matching problem, not a sum: each colored pip needs a source that
can make its color, and a dual land pays only one pip. Pips are assigned by
augmenting paths, so a payment is found whenever one exists — a greedy pass
can spend the only Island-capable source on a pip a Forest could have paid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

_SYMBOL = re.compile(r"\{([^}]+)\}")
_PAYABLE = frozenset("WUBRGC")
#: Spend floating mana first and Treasure last: a Treasure kept is mana later.
_RANK = {"pool": 0, "land": 1, "rock": 2, "treasure": 3}


@lru_cache(maxsize=None)
def parse_cost(mana_cost: str) -> tuple[int, tuple[frozenset[str], ...]]:
    """Split a cost into (generic, colored pips). Each pip is the set of colors
    that can pay it.

    {X} costs 0: the sim casts X spells for their minimum. A hybrid pip accepts
    either color. A Phyrexian pip needs its color, since the sim never pays
    life, and {2/W} is paid as {W}. Snow mana is paid as generic.
    """
    generic = 0
    pips: list[frozenset[str]] = []
    for symbol in _SYMBOL.findall(mana_cost.partition("//")[0]):
        symbol = symbol.upper()
        if symbol.isdigit():
            generic += int(symbol)
        elif symbol == "S":
            generic += 1
        elif symbol in _PAYABLE:
            pips.append(frozenset(symbol))
        elif "/" in symbol:
            colors = frozenset(part for part in symbol.split("/") if part in _PAYABLE)
            if colors:
                pips.append(colors)
    return generic, tuple(pips)


@dataclass(frozen=True)
class Unit:
    """One mana that can be spent right now."""

    colors: frozenset[str]
    #: "pool" (already floating), "land", "rock" (any nonland source), "treasure".
    kind: str
    #: Index into the mana pool or the battlefield; -1 for a Treasure.
    ref: int


def plan_payment(units: list[Unit], generic: int,
                 pips: tuple[frozenset[str], ...]) -> list[int] | None:
    """Indices into `units` that pay the cost, or None if it cannot be paid.

    Narrow sources are preferred over flexible ones, so a Command Tower is kept
    for the pip only it can pay.
    """
    order = sorted(range(len(units)), key=lambda i: (_RANK[units[i].kind], len(units[i].colors)))
    match: dict[int, int] = {}  # unit index -> pip index

    def assign(pip: int, seen: set[int]) -> bool:
        for unit in order:
            if unit in seen or not (units[unit].colors & pips[pip]):
                continue
            seen.add(unit)
            if unit not in match or assign(match[unit], seen):
                match[unit] = pip
                return True
        return False

    for pip in range(len(pips)):
        if not assign(pip, set()):
            return None
    rest = [unit for unit in order if unit not in match]
    if len(rest) < generic:
        return None
    return list(match) + rest[:generic]
