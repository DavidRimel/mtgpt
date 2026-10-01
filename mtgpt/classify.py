# mtgpt/classify.py
"""Tag each card with the functions it performs.

Classification is heuristic and deliberately visible: the report prints tags
per card so a wrong tag can be spotted and corrected, rather than silently
skewing the ratios it feeds.

Two rules deserve their reasoning stated:

* Ramp needs a mana-adding pattern in the oracle text. Scryfall's
  `produced_mana` is populated for cards that merely make Treasure or similar,
  so the field alone produces false positives.
* A search restricted to lands is ramp, not a tutor. Bracket rules count
  tutors as combo assembly and answer-finding; counting Cultivate would
  misreport tutor density.
"""

from __future__ import annotations

import re

from .models import Card, Function, ResolvedDeck

F = Function

_ADDS_MANA = re.compile(
    r"(\{T\}\s*:\s*Add|\badd one mana\b|\badd two mana\b|\badd \{|"
    r"\badds? .{0,20}mana\b|create .{0,20}\bTreasure\b)",
    re.IGNORECASE,
)
_SEARCH_LIBRARY = re.compile(r"search your library", re.IGNORECASE)
#: A land fetch names either the word "land" or a basic land TYPE. Nature's Lore,
#: Three Visits, and Farseek say "Forest card" / "Plains ... card" and never the
#: word "land", so omitting the type names misfiles the format's most-played ramp
#: spells as tutors — understating ramp and inflating tutor density at once.
_LAND_SEARCH = re.compile(
    r"search your library for (?:up to )?(?:a|an|one|two|three|four|X|\d+)?\s*"
    r"(?:basic )?(?:lands?|Plains|Island|Swamp|Mountain|Forest|Wastes)\b",
    re.IGNORECASE,
)
_DRAW = re.compile(
    r"\bdraws?\s+(?:a\s+card|one|two|three|four|five|six|seven|eight|nine|ten|X|\d+)\b",
    re.IGNORECASE,
)
#: "Whenever an opponent draws a card" is not card draw for us. Masked out before
#: _DRAW runs, so Smothering Tithe counts as ramp only.
_OPPONENT_DRAW = re.compile(
    r"\bopponents?\s+draws?\s+(?:a\s+card|\w+\s+cards?)", re.IGNORECASE
)
#: Bounce is removal. "owner's hand" is what separates it from graveyard
#: recursion, which returns to "your hand".
_SPOT_REMOVAL = re.compile(
    r"(destroy|exile)\s+target\b|"
    r"target\s+(creature|permanent|player)\s+(?:gets|sacrifices)|"
    r"return target .{0,60}?to (?:its|their) owner'?s hand",
    re.IGNORECASE,
)
#: Not every wipe says "destroy". Blasphemous Act deals damage to each creature;
#: Toxic Deluge gives all creatures -X/-X.
_SWEEPER = re.compile(
    r"(destroy|exile)\s+(all|each|every)\b|"
    r"each player sacrifices|"
    r"deals \S+ damage to each (?:creature|other creature)|"
    r"all creatures get -",
    re.IGNORECASE,
)
#: Mass land denial. "lands" need not sit immediately after "all": Jokulhaups
#: destroys "all artifacts, creatures, and lands" and Devastation "all creatures
#: and lands". Requiring adjacency misses the format's defining MLD cards, and
#: MLD is the rule brackets 1-3 care most about. [^.] keeps the match inside one
#: sentence; \blands?\b will not fire on "nonland".
_MASS_LAND_DENIAL = re.compile(
    r"(?:destroy|exile)\s+(?:all|each)\b[^.]{0,60}?\blands?\b|"
    r"each player sacrifices\s+(?:a|an|all|X|\d+)?\s*lands?\b",
    re.IGNORECASE,
)
#: Denial that hits ONLY lands. Armageddon is not a creature sweeper, but
#: Jokulhaups genuinely is both, so only the lands-only form suppresses SWEEPER.
_LANDS_ONLY_DENIAL = re.compile(r"(?:destroy|exile)\s+(?:all|each)\s+lands?\b", re.IGNORECASE)
#: Real counterspells rarely read "counter target spell": Swan Song says
#: "Counter target enchantment, instant, or sorcery spell", Dovin's Veto says
#: "noncreature spell". A window between "target" and "spell" catches them.
_COUNTERSPELL = re.compile(r"counter target\b.{0,60}?\b(?:spell|ability)\b", re.IGNORECASE)
_PROTECTION = re.compile(
    r"\bhexproof\b|\bindestructible\b|protection from|\bphases? out\b|"
    r"\bshroud\b|can't be countered|sacrifice .{0,30}\binstead\b",
    re.IGNORECASE,
)
#: "takes an extra turn", but also Time Stretch's "takes two extra turns".
_EXTRA_TURNS = re.compile(r"takes?\s+\w+\s+extra\s+turns?", re.IGNORECASE)
_WINCON = re.compile(r"\bwins? the game\b|\bloses? the game\b", re.IGNORECASE)
_RECURSION = re.compile(
    r"return .{0,60}from (?:your|a|target player's) graveyard", re.IGNORECASE
)


def classify(card: Card) -> frozenset[Function]:
    """Return every function this card performs.

    Lands short-circuit: a land is tagged LAND and nothing else, so a utility
    land's tap-for-mana text does not also register it as ramp.
    """
    if card.is_land:
        return frozenset({F.LAND})

    text = card.oracle_text or ""
    tags: set[Function] = set()

    if _is_ramp(card, text):
        tags.add(F.RAMP)
    if _SEARCH_LIBRARY.search(text) and not _LAND_SEARCH.search(text):
        tags.add(F.TUTOR)
    if _DRAW.search(_OPPONENT_DRAW.sub(" ", text)):
        tags.add(F.DRAW)
    if _COUNTERSPELL.search(text):
        tags.add(F.COUNTERSPELL)
    if _MASS_LAND_DENIAL.search(text):
        tags.add(F.MASS_LAND_DENIAL)
    # Lands-only denial is not a creature sweeper; a list-form wipe like
    # Jokulhaups is both, so only suppress on the lands-only form.
    if _SWEEPER.search(text) and not _LANDS_ONLY_DENIAL.search(text):
        tags.add(F.SWEEPER)
    # A counterspell is its own category, never spot removal.
    if (
        _SPOT_REMOVAL.search(text)
        and F.SWEEPER not in tags
        and F.COUNTERSPELL not in tags
    ):
        tags.add(F.SPOT_REMOVAL)
    if _PROTECTION.search(text) or _has_protection_keyword(card):
        tags.add(F.PROTECTION)
    if _EXTRA_TURNS.search(text):
        tags.add(F.EXTRA_TURNS)
    if _WINCON.search(text):
        tags.add(F.WINCON)
    if _RECURSION.search(text):
        tags.add(F.RECURSION)

    return frozenset(tags) if tags else frozenset({F.SYNERGY})


def _is_ramp(card: Card, text: str) -> bool:
    """Ramp is either an explicit mana-adding effect or a land fetch.

    Requiring the text pattern is what keeps `produced_mana` noise out.
    """
    if _LAND_SEARCH.search(text):
        return True
    if not _ADDS_MANA.search(text):
        return False
    # A creature or artifact that adds mana is ramp; a land was excluded above.
    return True


def _has_protection_keyword(card: Card) -> bool:
    protective = {"Hexproof", "Indestructible", "Shroud", "Ward"}
    return bool(protective & set(card.keywords))


def classify_deck(deck: ResolvedDeck) -> dict[str, frozenset[Function]]:
    """Classify every distinct card in the deck, keyed by card name."""
    tags = {card.name: classify(card) for _, card in deck.cards}
    for commander in deck.commanders:
        tags[commander.name] = classify(commander)
    return tags
