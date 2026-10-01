"""Typed values passed between pipeline stages.

Every dataclass is frozen so a stage cannot mutate its input. Collection
fields use tuple/frozenset to keep instances hashable.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass


#: Cards that relax singleton, e.g. Relentless Rats ("any number") and Seven
#: Dwarves ("up to seven"). The quantity is captured, not discarded: treating
#: "up to seven" as an unlimited exemption reports a deck with 20 Seven Dwarves
#: as legal.
_ANY_NUMBER_RE = re.compile(
    r"a deck can have (?:(any number of)|up to (\w+)) cards named", re.IGNORECASE
)

#: Number words that have appeared in a "up to N cards named" clause, plus
#: headroom. An unrecognized word yields no cap rather than a wrong one.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


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
    def copy_limit(self) -> int | None:
        """How many copies the card's own text allows. None means unlimited.

        1 is the ordinary singleton rule. Seven Dwarves returns 7 and Nazgul
        returns 9, so a deck running more than the stated number is still a
        violation — the earlier `allows_any_number` flag exempted them outright.
        A number word this does not recognize returns None: declining to cap is
        the safe direction, since a wrong cap invents a violation.
        """
        match = _ANY_NUMBER_RE.search(self.oracle_text or "")
        if match is None:
            return 1
        if match.group(1):
            return None
        return _NUMBER_WORDS.get((match.group(2) or "").lower())

    @property
    def allows_any_number(self) -> bool:
        """True when the card's own text relaxes singleton at all.

        Says nothing about by how much; `copy_limit` is what enforcement must
        use.
        """
        return self.copy_limit != 1

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
    #: False when Scryfall's Game Changers list could not be fetched, so every
    #: `Card.is_game_changer` on this deck is False by default rather than by
    #: verification. Last field, and defaulted, so positional construction
    #: elsewhere is unaffected. Consumers that enforce a Game Changer allowance
    #: must report the gap instead of returning a clean verdict.
    game_changers_available: bool = True

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
