# mtgpt/effects.py
"""What a card does in a goldfish game.

The simulator models value, not rules: the mana a card makes, the lands it
fetches, the cards it draws, what it tutors, the power it brings to combat, and
whether it is interaction to be held. Everything else in a card's text is
ignored, and `is_unmodeled` names the cards where that leaves nothing — so the
user can see what the sim skipped and add a goal-file override if it matters.

Parsing reads the front face's oracle text line by line. Two rules cut across
every effect:

* A "Whenever" line is a trigger whose cause the sim cannot see (an opponent
  drawing, a creature dying), so it is skipped. Triggers are modeled by the
  goal file's engine overrides instead.
* An activated ability that costs mana nets output minus cost: "{1}, {T}: Add
  {B}{B}" produces 2 mana but costs 1 to activate, netting 1 mana counted when
  positive. Multiple {T}-Add lines are alternatives (tapping is the cost), so
  mana = max units produced across lines, and mana_colors = union of colors.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .classify import _LAND_SEARCH, _OPPONENT_DRAW, classify
from .models import Card, Function

_ANY = frozenset("WUBRG")

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7,
}

#: "{T}: Add {G}." and "{T}, Pay 1 life: Add one mana of any color."
_ACTIVATED_ADD = re.compile(
    r"^(?P<cost>[^:]*\{T\}[^:]*):\s*Add (?P<clause>[^.]*)", re.IGNORECASE
)
#: A mana symbol in an activation cost, once {T} is removed.
_MANA_IN_COST = re.compile(r"\{(?:\d+|[WUBRGCX])\}")
#: A ritual's line: "Add {B}{B}{B}."
_RITUAL_ADD = re.compile(r"^Add (?P<clause>[^.]*)", re.IGNORECASE)
_SYMBOLS = re.compile(r"\{([WUBRGC])\}")
_N_MANA = re.compile(r"\b(one|two|three|four|five)\s+mana\b", re.IGNORECASE)
_TREASURE = re.compile(
    r"\bcreate (a|an|one|two|three|four|five|\d+) Treasure", re.IGNORECASE
)
_DRAW_N = re.compile(
    r"\bdraws? (a|one|two|three|four|five|\d+) cards?\b", re.IGNORECASE
)
_FETCH_COUNT = re.compile(
    r"search your library for (?:up to )?(a|an|one|two|three|\d+)\b", re.IGNORECASE
)
_TUTOR = re.compile(
    r"search your library for (?:a|an) (?P<what>[^.]*?)\bcards?\b", re.IGNORECASE
)
#: "This land enters tapped unless you control..." is read as untapped: the
#: condition is usually met in a two-color deck, and the sim cannot check it.
_ENTERS_TAPPED = re.compile(r"enters(?: the battlefield)? tapped(?!\s+unless)", re.IGNORECASE)
_POWER_BONUS = re.compile(r"(?:equipped|enchanted) creature gets \+(\d+)/", re.IGNORECASE)
_WIPE_PROOF = re.compile(r"indestructible|phases? out", re.IGNORECASE)
#: The card types a tutor restriction can name, matched against type lines.
_TUTOR_TYPES = (
    "creature", "artifact", "enchantment", "equipment", "aura",
    "instant", "sorcery", "planeswalker", "legendary",
)

_HELD = {
    Function.SPOT_REMOVAL: "removal",
    Function.SWEEPER: "sweeper",
    Function.PROTECTION: "protection",
    Function.COUNTERSPELL: "counterspell",
}
#: SimEffect fields stored as frozensets, which JSON writes as sorted lists.
_FROZEN_FIELDS = ("mana_colors", "land_colors", "held")


@dataclass(frozen=True)
class SimEffect:
    """What one card does in the sim. Every field defaults to doing nothing."""

    #: Mana a nonland permanent adds each turn, and the colors it can be.
    mana: int = 0
    mana_colors: frozenset[str] = frozenset()
    #: One-shot mana on resolution: a ritual's floating mana, or Treasures.
    mana_once: int = 0
    treasure_once: int = 0
    #: For lands, and MDFCs played as their land face.
    land_colors: frozenset[str] = frozenset()
    enters_tapped: bool = False
    #: Basic lands a ramp spell fetches.
    fetch_battlefield: int = 0
    fetch_hand: int = 0
    fetch_tapped: bool = False
    draw_once: int = 0
    draw_per_turn: int = 0
    #: "any", or type words joined by "|" ("instant|sorcery") one of which a
    #: target's type line must contain. None when the card is not a tutor.
    tutor: str | None = None
    power: float = 0.0
    #: +N power from an Equipment or Aura. The sim attaches it to the commander.
    power_bonus: int = 0
    #: Interaction the policy holds instead of casting: removal, sweeper,
    #: protection, counterspell.
    held: frozenset[str] = frozenset()
    #: Protection that survives a board wipe: indestructible or phasing.
    wipe_proof: bool = False

    @property
    def is_ramp(self) -> bool:
        return bool(
            self.mana or self.mana_once or self.treasure_once
            or self.fetch_battlefield or self.fetch_hand
        )

    @property
    def is_modeled(self) -> bool:
        """True when the sim gives this card anything to do beyond its power."""
        return bool(
            self.is_ramp or self.draw_once or self.draw_per_turn or self.tutor
            or self.power_bonus or self.held
        )


def effect_of(card: Card, identity: frozenset[str] = _ANY) -> SimEffect:
    """Parse what `card` does in a deck whose color identity is `identity`.

    `identity` bounds "any color": Birds of Paradise in a mono-green deck makes
    green, and Command Tower makes the commander's colors.
    """
    identity = frozenset(identity) or frozenset({"C"})
    if card.is_land:
        return _land_effect(card, identity)

    text = card.oracle_text or ""
    is_spell = any(t in card.front_type_line for t in ("Instant", "Sorcery"))

    # Collect all {T}: Add lines to find the max mana (alternatives, not sum)
    activated_lines = []
    mana_once = treasure = draw_once = draw_turn = 0
    fetch_bf = fetch_hand = 0
    fetch_tapped = False
    tutor = None

    for line in (part.strip() for part in text.split("\n")):
        if not line or line.lower().startswith("whenever"):
            continue
        activated = _ACTIVATED_ADD.match(line)
        if activated:
            activated_lines.append((activated.group("cost"), activated.group("clause")))
            continue
        ritual = _RITUAL_ADD.match(line)
        if ritual and is_spell:
            mana_once += _add_clause(ritual.group("clause"), identity)[0]
        treasure_match = _TREASURE.search(line)
        if treasure_match:
            treasure += _count(treasure_match.group(1))
        if _LAND_SEARCH.search(line):
            bf, hand, tapped = _fetch(line)
            fetch_bf += bf
            fetch_hand += hand
            fetch_tapped = fetch_tapped or tapped
        elif tutor is None:
            tutor_match = _TUTOR.search(line)
            if tutor_match:
                tutor = _restriction(tutor_match.group("what"))
        draw_match = _DRAW_N.search(_OPPONENT_DRAW.sub(" ", line))
        if draw_match and not _is_activated(line):
            n = _count(draw_match.group(1))
            if line.lower().startswith("at the beginning"):
                draw_turn += n
            else:
                draw_once += n

    # Process activated {T}: Add lines - take max across alternatives, union colors
    mana, colors = 0, frozenset()
    for cost, clause in activated_lines:
        cost_without_t = cost.replace("{T}", "")
        mana_symbols_in_cost = _MANA_IN_COST.findall(cost_without_t)

        # Calculate cost in mana value
        cost_mana = 0
        for symbol in mana_symbols_in_cost:
            # symbol is like "{2}", "{W}", etc. Strip the braces
            inner = symbol[1:-1]  # Remove { and }
            if inner.isdigit():
                cost_mana += int(inner)
            else:  # Colored or C symbol
                cost_mana += 1

        # Parse produced mana
        produced, produced_colors = _add_clause(clause, identity)

        # Net mana = produced - cost, only count if positive
        net = produced - cost_mana
        if net > 0:
            mana = max(mana, net)
            colors |= produced_colors

    tags = classify(card)
    held = frozenset(name for fn, name in _HELD.items() if fn in tags)
    is_creature = "Creature" in card.front_type_line
    is_attachment = any(t in card.front_type_line for t in ("Equipment", "Aura"))
    bonus = _POWER_BONUS.search(text) if is_attachment else None

    return SimEffect(
        mana=mana,
        mana_colors=colors,
        mana_once=mana_once,
        treasure_once=treasure,
        land_colors=_mdfc_colors(card, identity) if card.is_mdfc_land else frozenset(),
        fetch_battlefield=fetch_bf,
        fetch_hand=fetch_hand,
        fetch_tapped=fetch_tapped,
        draw_once=draw_once,
        draw_per_turn=draw_turn,
        tutor=tutor,
        power=(card.power or 0.0) if is_creature else 0.0,
        power_bonus=int(bonus.group(1)) if bonus else 0,
        held=held,
        wipe_proof="protection" in held and bool(_WIPE_PROOF.search(text)),
    )


def is_unmodeled(card: Card, effect: SimEffect) -> bool:
    """True when the card has rules text the sim gives nothing to do.

    Keyword-only text ("Flying, vigilance") has no sentence in it and is not
    reported: there is nothing there a user would want an override for.
    """
    if card.is_land or effect.is_modeled:
        return False
    return "." in (card.oracle_text or "")


def effect_to_dict(effect: SimEffect) -> dict:
    data = asdict(effect)
    for name in _FROZEN_FIELDS:
        data[name] = sorted(data[name])
    return data


def effect_from_dict(data: dict) -> SimEffect:
    return SimEffect(
        **{k: frozenset(v) if k in _FROZEN_FIELDS else v for k, v in data.items()}
    )


def _land_effect(card: Card, identity: frozenset[str]) -> SimEffect:
    text = card.oracle_text or ""
    colors = frozenset(card.produced_mana) & (identity | {"C"})
    tapped = bool(_ENTERS_TAPPED.search(text))

    # Shockland case: "If you don't, it enters tapped" is optimistic (assume payment made)
    if tapped and "if you don't" in text.lower():
        # Check for the specific shockland pattern to avoid false positives
        lower_text = text.lower()
        if re.search(r"if you don't[^.]*?(?:this land |it )enters tapped", lower_text):
            tapped = False

    if not colors and _LAND_SEARCH.search(text):
        # A fetchland makes no mana itself; it becomes the basic it finds.
        # Modeled as that basic, in any of the deck's colors.
        colors = identity
        tapped = "battlefield tapped" in text.lower()
    return SimEffect(land_colors=colors, enters_tapped=tapped)


def _mdfc_colors(card: Card, identity: frozenset[str]) -> frozenset[str]:
    """The colors an MDFC's land face makes. Scryfall's top-level
    `produced_mana` covers the back face; fall back to the card's colors."""
    colors = frozenset(card.produced_mana) & (identity | {"C"})
    return colors or (frozenset(card.colors) & identity) or identity


def _add_clause(clause: str, identity: frozenset[str]) -> tuple[int, frozenset[str]]:
    """How many mana an "Add ..." clause makes, and in which colors."""
    lower = clause.lower()
    if "any color" in lower or "any one color" in lower or "any combination of colors" in lower:
        amount = _N_MANA.search(clause)
        return (_count(amount.group(1)) if amount else 1), identity
    symbols = _SYMBOLS.findall(clause)
    if not symbols:
        return 0, frozenset()
    if " or " in lower:
        return 1, frozenset(symbols)
    return len(symbols), frozenset(symbols)


def _fetch(line: str) -> tuple[int, int, bool]:
    """(to battlefield, to hand, battlefield copies enter tapped)."""
    match = _FETCH_COUNT.search(line)
    count = _count(match.group(1)) if match else 1
    lower = line.lower()
    tapped = "onto the battlefield tapped" in lower
    if "the other into your hand" in lower:
        return 1, max(0, count - 1), tapped
    if "onto the battlefield" in lower:
        return count, 0, tapped
    return 0, count, False


def _restriction(what: str) -> str:
    # Strip words starting with "non" (noncreature, nonartifact, etc.)
    stripped = re.sub(r"\bnon\w+\s*", "", what.lower())
    found = [t for t in _TUTOR_TYPES if t in stripped]
    return "|".join(found) if found else "any"


def _is_activated(line: str) -> bool:
    """An activated ability's cost sits before a colon and contains a symbol."""
    cost, colon, _ = line.partition(":")
    return bool(colon) and "{" in cost


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word.lower(), 0)
