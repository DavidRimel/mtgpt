"""Card and deck builders for the goldfish tests.

Plain functions rather than fixtures, so a test reads as the deck it builds.
Imported as `from simdeck import ...`: pytest puts `tests/` on sys.path.
"""

from __future__ import annotations

import re

from mtgpt.goldfish.engine import Permanent, find_card, new_game, prepare
from mtgpt.models import Card, ResolvedDeck


def card(name, type_line="Artifact", oracle_text="", *, mana_cost="",
         produced_mana="", power=None, keywords=(), identity="G", colors=None):
    """A Card with sensible defaults. Mana value is computed from `mana_cost`."""
    symbols = re.findall(r"\{([^}]+)\}", mana_cost)
    mana_value = float(sum(int(s) if s.isdigit() else (0 if s == "X" else 1) for s in symbols))
    return Card(
        name=name,
        mana_value=mana_value,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost=mana_cost,
        color_identity=frozenset(identity),
        colors=frozenset(identity if colors is None else colors),
        legal_commander="legal",
        produced_mana=frozenset(produced_mana),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=tuple(keywords),
        power=power,
        toughness=power,
    )


def forest():
    return card("Forest", "Basic Land — Forest", "({T}: Add {G}.)",
                produced_mana="G", colors="")


def commander(mana_cost="{2}{G}{G}", power=4.0, name="Test Commander"):
    return card(name, "Legendary Creature — Elf", "", mana_cost=mana_cost, power=power)


SOL_RING = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", mana_cost="{1}",
                produced_mana="C", colors="")
LLANOWAR = card("Llanowar Elves", "Creature — Elf Druid", "{T}: Add {G}.",
                mana_cost="{G}", produced_mana="G", power=1.0)
RAMPANT_GROWTH = card(
    "Rampant Growth", "Sorcery",
    "Search your library for a basic land card, put that card onto the "
    "battlefield tapped, then shuffle.", mana_cost="{1}{G}")
NIGHTS_WHISPER = card("Night's Whisper", "Sorcery",
                      "You draw two cards and you lose 2 life.", mana_cost="{1}{G}")
DEMONIC_TUTOR = card("Demonic Tutor", "Sorcery",
                     "Search your library for a card, put that card into your hand, "
                     "then shuffle.", mana_cost="{1}{G}")
SWORDS = card("Swords to Plowshares", "Instant",
              "Exile target creature. Its controller gains life equal to its power.",
              mana_cost="{G}")
TEFERIS_PROTECTION = card(
    "Teferi's Protection", "Instant",
    "Until your next turn, your life total can't change and you gain protection "
    "from everything. All permanents you control phase out.", mana_cost="{2}{G}")
COUNTERSPELL = card("Counterspell", "Instant", "Counter target spell.", mana_cost="{G}{G}")
BEAR = card("Grizzly Bears", "Creature — Bear", "", mana_cost="{1}{G}", power=2.0)


def deck(*spells, cmdr=None, lands=None):
    """A 99 of `spells` padded with Forests, under one commander."""
    lands = 99 - len(spells) if lands is None else lands
    cards = [(1, s) for s in spells] + [(lands, forest())]
    return ResolvedDeck(commanders=(cmdr or commander(),), cards=tuple(cards))


NEVER = {"opponent_life_lost": 100000}


def rigged(*spells, hand=(), lands_in_play=0, on_board=(), commander_out=False,
           goal=None, source=None, land="Forest", **game):
    """A game on turn 1 with an exact hand and board, whatever the shuffle dealt.

    `source` replaces the default deck of `spells` plus Forests; `land` names
    the land `lands_in_play` puts out.
    """
    setup = prepare(source or deck(*spells), goal or {
        "archetype": "custom", "thing": "commander", "win": NEVER})
    s = new_game(setup, seed=1, **game)
    s.library += s.hand
    s.hand = []
    for name in hand:
        idx = find_card(s, name, s.library)
        s.library.remove(idx)
        s.hand.append(idx)
    for name in [land] * lands_in_play + list(on_board):
        idx = find_card(s, name, s.library)
        s.library.remove(idx)
        c = s.cards[idx]
        s.battlefield.append(Permanent(card=idx, name=name, power=c.effect.power,
                                       is_creature=c.is_creature, entered=0, is_land=c.is_land))
    if commander_out:
        idx = s.command_zone.pop()
        s.battlefield.append(Permanent(card=idx, name=s.cards[idx].name,
                                       power=s.cards[idx].effect.power, is_creature=True, entered=0))
    return s
