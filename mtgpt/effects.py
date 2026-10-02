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
    r"search your library(?: and/or graveyard)? for (?:a|an) (?P<what>[^.]*?)\bcards?\b", re.IGNORECASE
)
#: "...with mana value X or less" (Chord of Calling, Green Sun's Zenith).
_TUTOR_X = re.compile(r"search your library[^.]*with mana value x or less", re.IGNORECASE)
#: "...put it/that card onto the battlefield" in the tutor's sentence.
_TUTOR_BATTLEFIELD = re.compile(r"search your library[^.]*?(?:put (?:it|that card) onto the battlefield)",
                                re.IGNORECASE)
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
#: "This land enters tapped unless you control..." is read as untapped: the
#: condition is usually met in a two-color deck, and the sim cannot check it.
_ENTERS_TAPPED = re.compile(r"enters(?: the battlefield)? tapped(?!\s+unless)", re.IGNORECASE)
_POWER_BONUS = re.compile(r"(?:equipped|enchanted) creature gets \+(\d+)/", re.IGNORECASE)
_WIPE_PROOF = re.compile(r"indestructible|phases? out", re.IGNORECASE)
#: Enter the Infinite: "Draw cards equal to the number of cards in your library".
_DRAW_LIBRARY = re.compile(r"draw cards equal to the number of cards in your library", re.IGNORECASE)
#: "...then put a card from your hand on top of your library".
_PUT_BACK = re.compile(r"put (a|one|two|three) cards? from your hand on top of your library",
                       re.IGNORECASE)
#: "Take an extra turn after this one", "takes two extra turns".
_EXTRA_TURN_COUNT = re.compile(r"\btakes? (an|one|two|three|\d+) extra turns?", re.IGNORECASE)
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
    #: Extra turns the spell grants (Time Stretch is 2).
    extra_turns: int = 0
    #: The spell shuffles itself into the library instead of the graveyard
    #: (Nexus of Fate, Beacon of Tomorrows).
    shuffle_self: bool = False
    #: Draw the whole library (Enter the Infinite), then put this many cards
    #: from hand back on top.
    draw_library: bool = False
    put_back: int = 0
    #: What a land search may find: "basic", "land", or land types joined by
    #: "|" ("forest", "plains|island|swamp|mountain").
    fetch_types: str = ""
    #: A cost any spell may be paid with instead of its own while this is on
    #: the battlefield (Jodah, Leyline of Mutation; Omniscience is "{0}").
    alt_cost: str | None = None
    alt_cost_hand_only: bool = False
    #: Begins the game on the battlefield from the opening hand (Leylines).
    leyline: bool = False
    #: Mana that can't pay generic costs (Jegantha).
    colored_only: bool = False
    #: Cards a tutor finds (Conflux finds five). 0 means one.
    tutor_count: int = 0
    #: Lands you control tap for any color (Chromatic Lantern, Dryad).
    lands_any_color: bool = False
    #: All your mana may be spent as any color (Chromatic Orrery).
    mana_any_color: bool = False
    extra_land_drops: int = 0
    #: You may play lands from the top of your library (Oracle of Mul Daya).
    lands_from_top: bool = False
    landfall_mana: int = 0
    landfall_treasure: int = 0
    #: Taps for one mana of each color among your permanents (Bloom Tender).
    mana_per_color: bool = False
    cascade: int = 0
    #: Your spells with at least this mana value have cascade (Imoti); 0 = none.
    grants_cascade_min: int = 0
    #: Approach of the Second Sun: the second cast from hand wins.
    approach: bool = False
    #: Look at the top N; permanents to the battlefield, the rest to hand (Genesis Ultimatum).
    dig_permanents: int = 0
    #: Look at the top `dig_look`, keep `dig_take` (Dig Through Time).
    dig_look: int = 0
    dig_take: int = 0
    delve: bool = False
    #: Spells per turn castable without paying (One with the Multiverse).
    free_spell_per_turn: int = 0
    #: Emergent Ultimatum: find N monocolored cards, lose one, cast the rest free.
    emergent: int = 0
    #: Sacrifice to put a card of this type on top (Sterling Grove).
    sac_tutor_top: str | None = None
    #: Cards drawn per opponent draw (Consecrated Sphinx).
    opp_draw_cards: int = 0
    #: A Treasure on opponents' draws (Smothering Tithe).
    opp_draw_treasure: bool = False
    #: A static hoser that stops opponents' win attempts while it is out.
    stax: bool = False
    #: Doesn't untap in your untap step (Mana Vault).
    no_untap: bool = False
    #: Pact: once spent, pay this at your next upkeep or lose (Pact of Negation).
    pact_cost: str | None = None
    #: A tutor that puts the card on top instead of in hand (Vampiric Tutor).
    tutor_to_top: bool = False
    #: An X tutor: it finds a card of mana value X or less, X paid on top of its cost.
    tutor_x: bool = False
    #: The tutor puts the card onto the battlefield instead of into hand.
    tutor_battlefield: bool = False
    #: A color the found card must have ("green creature card"), as a WUBRG letter.
    tutor_color: str | None = None
    #: Enters only by discarding a land from hand (Mox Diamond).
    discard_land: bool = False
    #: Exiles a card from hand on entering and taps for its colors (Chrome Mox).
    imprint: bool = False
    #: Wins on entering if your devotion to blue covers the library (Thassa's Oracle).
    thoracle: bool = False
    #: Exiles the whole library (Demonic Consultation, Tainted Pact naming a
    #: card not in the deck).
    exile_library: bool = False

    @property
    def is_ramp(self) -> bool:
        return bool(
            self.mana or self.mana_once or self.treasure_once
            or self.fetch_battlefield or self.fetch_hand
            or self.mana_per_color or self.landfall_mana or self.landfall_treasure
            or self.extra_land_drops or self.lands_any_color or self.mana_any_color
            or self.alt_cost or self.free_spell_per_turn or self.opp_draw_treasure
        )

    @property
    def is_modeled(self) -> bool:
        """True when the sim gives this card anything to do beyond its power."""
        return bool(
            self.is_ramp or self.draw_once or self.draw_per_turn or self.tutor
            or self.power_bonus or self.held or self.extra_turns or self.draw_library
            or self.alt_cost or self.leyline or self.lands_any_color or self.mana_any_color
            or self.extra_land_drops or self.lands_from_top or self.landfall_mana
            or self.landfall_treasure or self.mana_per_color or self.cascade
            or self.grants_cascade_min or self.approach or self.dig_permanents or self.dig_look
            or self.free_spell_per_turn or self.emergent or self.sac_tutor_top
            or self.opp_draw_cards or self.opp_draw_treasure or self.stax or self.imprint
            or self.thoracle or self.exile_library
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
        # An activated ability that costs mana ("{2}, {T}, Sacrifice: Search...")
        # is not a free effect of casting the card; a symbol-free cost
        # ("Sacrifice this creature: Search...") still counts.
        activated_cost = _is_activated(line)
        if _LAND_SEARCH.search(line):
            if not activated_cost:
                bf, hand, tapped = _fetch(line)
                fetch_bf += bf
                fetch_hand += hand
                fetch_tapped = fetch_tapped or tapped
        elif tutor is None and not activated_cost:
            tutor_match = _TUTOR.search(line)
            if tutor_match:
                tutor = _restriction(tutor_match.group("what"))
        draw_match = _DRAW_N.search(_OPPONENT_DRAW.sub(" ", line))
        if draw_match and not activated_cost:
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
        if net > 0 and re.search(r"sacrifice (?:this|~|" + re.escape(card.name) + ")", cost, re.IGNORECASE):
            # Sacrificed to make mana (Lotus Petal): once, like a Treasure.
            treasure = max(treasure, net)
        elif net > 0:
            mana = max(mana, net)
            colors |= produced_colors

    tags = classify(card)
    held = frozenset(name for fn, name in _HELD.items() if fn in tags)
    if _REDIRECT.search(text):
        held |= {"protection"}
    extra = _mechanics(card, text)
    if extra.get("stax") and is_spell:
        # An instant that stops opponents casting (Silence) answers a win
        # attempt the way a counterspell does.
        held |= {"counterspell"}
        extra.pop("stax")
    if extra.get("sylvan"):
        draw_turn += 1
    extra.pop("sylvan", None)
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
        extra_turns=_extra_turns(text),
        shuffle_self=is_spell and _shuffles_itself(card, text),
        draw_library=bool(_DRAW_LIBRARY.search(text)),
        put_back=_count(m.group(1)) if (m := _PUT_BACK.search(text)) else 0,
        enters_tapped=bool(_SELF_ENTERS_TAPPED.search(text)),
        fetch_types=_fetch_types(text) if fetch_bf or fetch_hand else "",
        **extra,
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


def _extra_turns(text: str) -> int:
    match = _EXTRA_TURN_COUNT.search(text)
    return _count(match.group(1)) if match else 0


def _shuffles_itself(card: Card, text: str) -> bool:
    """"Shuffle Beacon of Tomorrows into its owner's library", or Nexus of
    Fate's "shuffle it into its owner's library instead"."""
    name = re.escape(card.name.partition("//")[0].strip())
    return bool(re.search(rf"shuffle (?:it|{name}|this (?:card|spell)) into its owner's library",
                          text, re.IGNORECASE))


#: Deflecting Swat: redirecting removal is protection.
_REDIRECT = re.compile(r"choose new targets for target spell", re.IGNORECASE)
_SELF_ENTERS_TAPPED = re.compile(r"(?:^|\n|\. )[^.\n]{0,40}?\benters(?: the battlefield)? tapped\.",
                                 re.IGNORECASE)
_LAND_TYPES = ("plains", "island", "swamp", "mountain", "forest")
_FETCH_WHAT = re.compile(r"search your library for (?:up to \w+ )?(?:an? )?(?P<what>[^.]*?)\bcards?\b",
                         re.IGNORECASE)


def _fetch_types(text: str) -> str:
    match = _FETCH_WHAT.search(text)
    what = (match.group("what") if match else "").lower()
    if "basic" in what:
        return "basic"
    found = [t for t in _LAND_TYPES if t in what]
    return "|".join(found) if found else "land"


def _num(word: str) -> int:
    return _count(word) if not word.isdigit() else int(word)


def _mechanics(card: Card, text: str) -> dict:
    """The rarer mechanics, one pattern each. Returns only fields that apply."""
    t = text.lower()
    out: dict = {}
    if m := re.search(r"you may pay ((?:\{[^}]+\})+) rather than pay the mana cost for spells you cast", t):
        out["alt_cost"] = m.group(1).upper()
    if "you may cast spells from your hand without paying their mana costs" in t:
        out["alt_cost"], out["alt_cost_hand_only"] = "{0}", True
    if "if this card is in your opening hand, you may begin the game with it on the battlefield" in t:
        out["leyline"] = True
    if "this mana can't be spent to pay generic mana costs" in t:
        out["colored_only"] = True
    colors = re.findall(r"an? (?:white|blue|black|red|green) card", t)
    if "search your library for" in t and len(colors) >= 2:
        out["tutor_count"] = len(colors)
    if re.search(r'lands you control have "\{t\}: add one mana of any color', t) or \
            "lands you control are every basic land type" in t:
        out["lands_any_color"] = True
    if "spend mana as though it were mana of any color" in t:
        out["mana_any_color"] = True
    if "you may play an additional land on each of your turns" in t:
        out["extra_land_drops"] = 1
    if re.search(r"you may play lands(?: and cast spells)? from the top of your library", t):
        out["lands_from_top"] = True
    if "whenever a land you control enters, add one mana of any color" in t:
        out["landfall_mana"] = 1
    if re.search(r"whenever a land you control enters, create a (?:food token or a )?treasure", t):
        out["landfall_treasure"] = 1
    if "for each color among permanents you control, add one mana of that color" in t:
        out["mana_per_color"] = True
    if m := re.match(r"(cascade(?:, cascade)*)\b", t.split("\n")[0].strip()):
        out["cascade"] = m.group(1).count("cascade")
    if m := re.search(r"spells you cast with mana value (\d+) or greater have cascade", t):
        out["grants_cascade_min"] = int(m.group(1))
    if re.search(r"you've cast another spell named .+? this game, you win the game", t):
        out["approach"] = True
    if m := re.search(r"look at the top (\w+) cards of your library\. put any number of permanent "
                      r"cards from among them onto the battlefield and the rest into your hand", t):
        out["dig_permanents"] = _num(m.group(1))
    if m := re.search(r"look at the top (\w+) cards of your library\. put (\w+) of them into your hand", t):
        out["dig_look"], out["dig_take"] = _num(m.group(1)), _num(m.group(2))
    if t.startswith("delve"):
        out["delve"] = True
    if "once during each of your turns, you may cast a spell from your hand or the top of your " \
            "library without paying its mana cost" in t:
        out["free_spell_per_turn"] = 1
    if m := re.search(r"search your library for up to (\w+) monocolored cards with different names", t):
        out["emergent"] = _num(m.group(1))
    if m := re.search(r"sacrifice this enchantment: search your library for an? (\w+) card, reveal it, "
                      r"then shuffle and put that card on top", t):
        out["sac_tutor_top"] = m.group(1)
    if m := re.search(r"whenever an opponent draws a card, you may draw (\w+) cards", t):
        out["opp_draw_cards"] = _num(m.group(1))
    if re.search(r"whenever an opponent draws a card, .*you create a treasure", t):
        out["opp_draw_treasure"] = True
    if re.search(r"\b(?:your opponents|each opponent|players|your opponents' spells) can't (?:cast|search|win)", t) \
            or "spells your opponents cast cost" in t or "your opponents can't" in t and "spells" in t:
        out["stax"] = True
    if "if x is greater than or equal to the number of cards in your library, you win the game" in t:
        out["thoracle"] = True
    if ("reveal cards from the top of your library until you reveal a card with the chosen name" in t
            or "repeat this process until you put a card into your hand or you exile two cards with the same name" in t):
        out["exile_library"] = True
    if "doesn't untap during your untap step" in t:
        out["no_untap"] = True
    if m := re.search(r"at the beginning of your next upkeep, pay ((?:\{[^}]+\})+)\. if you don't, you lose the game", t):
        out["pact_cost"] = m.group(1).upper()
    if re.search(r"search your library for [^.]*?(?:then shuffle and )?put (?:that card|the card|it) on top", t):
        out["tutor_to_top"] = True
    if _TUTOR_X.search(t):
        out["tutor_x"] = True
    if _TUTOR_BATTLEFIELD.search(t):
        out["tutor_battlefield"] = True
    if (m := _TUTOR.search(t)) and not out.get("tutor_count"):
        colors = [c for word, c in _COLOR_WORDS.items() if re.search(rf"\b{word}\b", m.group("what").lower())]
        if len(colors) == 1:
            out["tutor_color"] = colors[0]
    if "you may discard a land card instead" in t:
        out["discard_land"] = True
    if "imprint — when this artifact enters, you may exile a nonartifact, nonland card from your hand" in t:
        out["imprint"] = True
    if "at the beginning of your draw step, you may draw two additional cards" in t:
        out["sylvan"] = True
    return out


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word.lower(), 0)
