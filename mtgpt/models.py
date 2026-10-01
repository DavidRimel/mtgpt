"""Typed values passed between pipeline stages.

Every dataclass is frozen so a stage cannot mutate its input. Collection
fields use tuple/frozenset to keep instances hashable.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass


#: Cards that exempt themselves from singleton, e.g. Relentless Rats ("any
#: number") and Seven Dwarves ("up to seven").
_ANY_NUMBER_RE = re.compile(
    r"a deck can have (?:any number of|up to \w+) cards named", re.IGNORECASE
)


class Function(enum.Enum):
    """What role a card plays in the deck.

    A card may carry several functions; Cultivate is both RAMP and TUTOR.
    """

    LAND = "land"
    RAMP = "ramp"
    DRAW = "draw"
    SPOT_REMOVAL = "spot_removal"
    SWEEPER = "sweeper"
    TUTOR = "tutor"
    COUNTERSPELL = "counterspell"
    PROTECTION = "protection"
    WINCON = "wincon"
    RECURSION = "recursion"
    MASS_LAND_DENIAL = "mass_land_denial"
    EXTRA_TURNS = "extra_turns"
    SYNERGY = "synergy"


class Severity(enum.IntEnum):
    """Lower value sorts first, so ERROR leads a sorted report."""

    ERROR = 0
    WARNING = 1
    INFO = 2


@dataclass(frozen=True, order=True)
class Violation:
    """A rule finding. `code` is stable for tests; `message` is for humans."""

    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class DeckEntry:
    """One parsed line of a decklist, before any Scryfall lookup."""

    qty: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    category: str | None = None
    is_commander: bool = False


@dataclass(frozen=True)
class ParsedDeck:
    """Output of deckparse. Names are unverified strings at this point."""

    entries: tuple[DeckEntry, ...] = ()
    commanders: tuple[DeckEntry, ...] = ()

    @property
    def total_cards(self) -> int:
        return sum(e.qty for e in self.entries)

    @property
    def total_with_commanders(self) -> int:
        return self.total_cards + sum(e.qty for e in self.commanders)


@dataclass(frozen=True)
class Card:
    """A Scryfall-verified card. If you hold one of these, the card is real."""

    name: str
    mana_value: float
    type_line: str
    oracle_text: str
    mana_cost: str
    color_identity: frozenset[str]
    colors: frozenset[str]
    legal_commander: str
    produced_mana: frozenset[str]
    layout: str
    is_game_changer: bool
    usd: float | None
    keywords: tuple[str, ...] = ()

    @property
    def front_type_line(self) -> str:
        """Type line of the front face only."""
        return self.type_line.split("//")[0].strip()

    @property
    def is_land(self) -> bool:
        """True only when the front face is a land.

        An MDFC whose back face is a land is not a land: you cannot play it as
        one on the turn you need the front half.
        """
        return "Land" in self.front_type_line

    @property
    def is_basic_land(self) -> bool:
        """True for basic lands, including snow basics.

        Scryfall types Snow-Covered Forest as "Basic Snow Land — Forest", so a
        startswith("Basic Land") test misses every snow basic and floods a snow
        deck with false singleton violations.
        """
        line = self.front_type_line
        return line.startswith("Basic") and "Land" in line

    @property
    def allows_any_number(self) -> bool:
        """True when the card's own text exempts it from the singleton rule."""
        return bool(_ANY_NUMBER_RE.search(self.oracle_text or ""))

    @property
    def is_mdfc_land(self) -> bool:
        """A spell on the front, a land on the back. Counts as a flex source."""
        if "//" not in self.type_line:
            return False
        front, _, back = self.type_line.partition("//")
        return "Land" not in front and "Land" in back

    @property
    def is_banned(self) -> bool:
        return self.legal_commander == "banned"

    @property
    def is_legal(self) -> bool:
        return self.legal_commander == "legal"


@dataclass(frozen=True)
class ResolvedDeck:
    """Output of scryfall.resolve. Every name is now a verified Card."""

    commanders: tuple[Card, ...] = ()
    cards: tuple[tuple[int, Card], ...] = ()

    @property
    def total_cards(self) -> int:
        return sum(qty for qty, _ in self.cards)

    @property
    def total_with_commanders(self) -> int:
        return self.total_cards + len(self.commanders)

    @property
    def command_zone_identity(self) -> frozenset[str]:
        """Union of the commanders' color identities."""
        out: set[str] = set()
        for card in self.commanders:
            out |= card.color_identity
        return frozenset(out)

    def iter_cards(self):
        """Yield each card once per copy, so quantities are respected."""
        for qty, card in self.cards:
            for _ in range(qty):
                yield card
