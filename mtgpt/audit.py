"""Measure a deck against the targets in targets.py.

Reports actual-versus-target for each category so drift is visible rather
than arguable.

Trust assumptions:
- Curve bucketing truncates (int()) rather than rounding. No Commander-legal card
  has a fractional mana value, so this is unobservable in practice.
- `_sources_for` trusts Card.produced_mana at face value. If Scryfall lists a color
  only conditionally available, the pip sources count overstates and `.ok` could
  read True for an inadequate mana base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import targets
from .classify import classify_deck
from .models import Function, ResolvedDeck

F = Function

#: Colored mana symbols in a mana cost. Generic, X, and colorless are excluded
#: because they place no demand on the mana base's colors.
_PIP_RE = re.compile(r"\{([WUBRG])\}")

#: Hybrid, phyrexian, and monocolored-hybrid symbols: {G/W}, {G/P}, {2/W}.
#: The left side may be generic, as on Spectral Procession's {2/W} — requiring a
#: color there reports zero pips for such a card and silently understates the
#: deck's color needs. Every listed color gets the pip, which overstates demand
#: slightly for hybrids; that is the safe direction to be wrong in.
_HYBRID_RE = re.compile(r"\{(?:([WUBRG])|\d+)/([WUBRGP])\}")

#: Mana values above this are grouped into one bucket.
CURVE_TOP_BUCKET = 7


@dataclass(frozen=True)
class CategoryCount:
    """How many cards fill a role, against the target band."""

    function: Function
    count: int
    target_min: int
    target_max: int

    @property
    def status(self) -> str:
        if self.count < self.target_min:
            return "low"
        if self.count > self.target_max:
            return "high"
        return "ok"

    @property
    def delta(self) -> int:
        """Cards to add (positive) or cut (negative) to reach the band."""
        if self.count < self.target_min:
            return self.target_min - self.count
        if self.count > self.target_max:
            return self.target_max - self.count
        return 0


@dataclass(frozen=True)
class PipReport:
    """Whether the mana base can support a color's heaviest demand."""

    color: str
    total_pips: int
    max_pips: int
    sources: int
    required: int

    @property
    def ok(self) -> bool:
        return self.sources >= self.required


@dataclass(frozen=True)
class AuditReport:
    """The full measurement of a deck."""

    total_cards: int
    land_count: int
    mdfc_land_count: int
    mana_sources: int
    average_mana_value: float
    curve: tuple[tuple[int, int], ...]
    categories: tuple[CategoryCount, ...]
    pips: tuple[PipReport, ...]

    @property
    def curve_status(self) -> str:
        low, high = targets.AVERAGE_MV_BAND
        if self.average_mana_value < low:
            return "low"
        if self.average_mana_value > high:
            return "high"
        return "ok"


def audit(deck: ResolvedDeck, tags: dict[str, frozenset[Function]] | None = None) -> AuditReport:
    """Measure the deck. Pass `tags` to reuse an existing classification."""
    tags = tags if tags is not None else classify_deck(deck)

    land_count = sum(qty for qty, c in deck.cards if c.is_land)
    mdfc_land_count = sum(qty for qty, c in deck.cards if c.is_mdfc_land)

    counts: dict[Function, int] = {}
    for qty, card in deck.cards:
        for function in tags.get(card.name, frozenset()):
            counts[function] = counts.get(function, 0) + qty

    ramp_count = counts.get(F.RAMP, 0)
    # A card could in principle be both ramp (by its front face's text) and an
    # MDFC land back, which would be counted twice below. No current card does,
    # but the sum must not depend on that staying true.
    ramp_and_mdfc = sum(
        qty
        for qty, card in deck.cards
        if card.is_mdfc_land and F.RAMP in tags.get(card.name, frozenset())
    )
    mana_sources = land_count + ramp_count + mdfc_land_count - ramp_and_mdfc

    categories = tuple(
        CategoryCount(
            function=function,
            count=counts.get(function, 0),
            target_min=band[0],
            target_max=band[1],
        )
        for function, band in targets.CATEGORY_TARGETS.items()
    )

    return AuditReport(
        total_cards=deck.total_cards,
        land_count=land_count,
        mdfc_land_count=mdfc_land_count,
        mana_sources=mana_sources,
        average_mana_value=_average_mana_value(deck),
        curve=_curve(deck),
        categories=categories,
        pips=_pips(deck, tags),
    )


def _nonlands(deck: ResolvedDeck):
    for qty, card in deck.cards:
        if not card.is_land:
            yield qty, card


def _average_mana_value(deck: ResolvedDeck) -> float:
    total = 0.0
    count = 0
    for qty, card in _nonlands(deck):
        total += card.mana_value * qty
        count += qty
    return round(total / count, 2) if count else 0.0


def _curve(deck: ResolvedDeck) -> tuple[tuple[int, int], ...]:
    buckets: dict[int, int] = {}
    for qty, card in _nonlands(deck):
        bucket = min(int(card.mana_value), CURVE_TOP_BUCKET)
        buckets[bucket] = buckets.get(bucket, 0) + qty
    return tuple(sorted(buckets.items()))


def _count_pips(mana_cost: str) -> dict[str, int]:
    """Colored pip demand for one card."""
    pips: dict[str, int] = {}
    for color in _PIP_RE.findall(mana_cost):
        pips[color] = pips.get(color, 0) + 1
    for left, right in _HYBRID_RE.findall(mana_cost):
        for color in (left, right):
            # `left` is empty when the symbol is a monocolored hybrid like {2/W}.
            if color and color in "WUBRG":
                pips[color] = pips.get(color, 0) + 1
    return pips


def _pips(deck: ResolvedDeck, tags: dict[str, frozenset[Function]]) -> tuple[PipReport, ...]:
    totals: dict[str, int] = {}
    maxima: dict[str, int] = {}

    for qty, card in _nonlands(deck):
        for color, count in _count_pips(card.mana_cost).items():
            totals[color] = totals.get(color, 0) + count * qty
            maxima[color] = max(maxima.get(color, 0), count)

    reports = []
    for color in sorted(totals):
        sources = _sources_for(deck, tags, color)
        demand = min(maxima[color], max(targets.PIP_SOURCE_MINIMUMS))
        reports.append(
            PipReport(
                color=color,
                total_pips=totals[color],
                max_pips=maxima[color],
                sources=sources,
                required=targets.PIP_SOURCE_MINIMUMS[demand],
            )
        )
    return tuple(reports)


def _sources_for(deck: ResolvedDeck, tags: dict[str, frozenset[Function]], color: str) -> int:
    """Cards that can produce `color`: lands, MDFC land backs, and ramp."""
    total = 0
    for qty, card in deck.cards:
        if color not in card.produced_mana:
            continue
        is_ramp = F.RAMP in tags.get(card.name, frozenset())
        if card.is_land or card.is_mdfc_land or is_ramp:
            total += qty
    return total
