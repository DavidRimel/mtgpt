"""Card mechanics the goldfish models beyond mana, draw, and tutors.

Each test uses the card's real oracle text, so a parsing change that stops
reading a printed card fails here first.
"""

import json

from mtgpt.effects import effect_of
from mtgpt.goldfish.engine import apply, available_mana, from_dict, legal_actions, to_dict
from mtgpt.models import ResolvedDeck

from simdeck import NEVER, card, commander, forest, rigged

PASS = {"pass": True}
WUBRG = frozenset("WUBRG")
G = frozenset("G")


def names(s, zone):
    return sorted(s.cards[i].name for i in zone)


def five_color(*spells, lands=None, land=None, cmdr=None):
    land = land or card("Prism Land", "Land", "{T}: Add one mana of any color.",
                        produced_mana="WUBRG", identity="WUBRG", colors="")
    lands = 99 - len(spells) if lands is None else lands
    five = card("Test Commander", "Legendary Creature — Elf", "", mana_cost="{2}{G}{G}",
                power=4.0, identity="WUBRG", colors="G")
    return ResolvedDeck(commanders=(cmdr or five,),
                        cards=tuple((1, s) for s in spells) + ((lands, land),))


# --- Land searches find typed nonbasics --------------------------------------

NATURES_LORE = card("Nature's Lore", "Sorcery", "Search your library for a Forest card, put that "
                    "card onto the battlefield, then shuffle.", mana_cost="{1}{G}")
BAYOU = card("Bayou", "Land — Swamp Forest", "({T}: Add {B} or {G}.)", produced_mana="BG",
             identity="BG", colors="")


def test_forest_search_finds_a_dual_with_the_forest_type():
    assert effect_of(NATURES_LORE, G).fetch_types == "forest"
    s = rigged(source=five_color(NATURES_LORE, BAYOU), land="Prism Land", lands_in_play=2,
               hand=["Nature's Lore"])
    s.command_zone = []
    s = apply(s, {"cast": "Nature's Lore"})
    assert "Bayou" in [p.name for p in s.battlefield]
    assert "Bayou" not in [s.cards[i].name for i in s.library]


# --- Alternative costs read from text ----------------------------------------

LEYLINE = card("Leyline of Mutation", "Enchantment", "If this card is in your opening hand, you may "
               "begin the game with it on the battlefield.\nYou may pay {W}{U}{B}{R}{G} rather than "
               "pay the mana cost for spells you cast.", mana_cost="{2}{G}{G}")
OMNISCIENCE = card("Omniscience", "Enchantment", "You may cast spells from your hand without paying "
                   "their mana costs.", mana_cost="{7}{U}{U}{U}")
BIG = card("Big Spell", "Sorcery", "", mana_cost="{9}{U}")


def test_alt_costs_are_read_from_card_text():
    assert effect_of(LEYLINE, WUBRG).alt_cost == "{W}{U}{B}{R}{G}"
    assert effect_of(OMNISCIENCE, WUBRG).alt_cost == "{0}"
    assert effect_of(OMNISCIENCE, WUBRG).alt_cost_hand_only


def test_leyline_alt_cost_lets_a_ten_drop_cost_five():
    s = rigged(source=five_color(LEYLINE, BIG), land="Prism Land", lands_in_play=5,
               on_board=["Leyline of Mutation"], hand=["Big Spell"])
    s.command_zone = []
    s = apply(s, {"cast": "Big Spell"})
    assert available_mana(s) == 0


def test_omniscience_is_free_from_hand_only():
    s = rigged(source=five_color(OMNISCIENCE, BIG), land="Prism Land", lands_in_play=0,
               on_board=["Omniscience"], hand=["Big Spell"])
    assert {"cast": "Big Spell"} in legal_actions(s)
    assert {"cast": "Test Commander"} not in legal_actions(s)  # command zone is not your hand


def test_leyline_in_the_opening_hand_starts_on_the_battlefield():
    from mtgpt.goldfish.engine import new_game, prepare
    setup = prepare(five_color(LEYLINE, lands=98), {"archetype": "go_wide"})
    for seed in range(40):
        s = new_game(setup, seed=seed)
        on_board = "Leyline of Mutation" in [p.name for p in s.battlefield]
        in_hand = "Leyline of Mutation" in names(s, s.hand)
        assert not in_hand or s.turn > 1  # never left sitting in the opening hand
        if on_board:
            return
    raise AssertionError("Leyline never appeared in 40 opening hands")


# --- Mana details ---------------------------------------------------------------

TIMELESS = card("Timeless Lotus", "Legendary Artifact", "Timeless Lotus enters tapped.\n{T}: Add "
                "{W}{U}{B}{R}{G}.", mana_cost="{5}")
JEGANTHA = card("Jegantha, the Wellspring", "Legendary Creature — Elemental Elk",
                "{T}: Add {W}{U}{B}{R}{G}. This mana can't be spent to pay generic mana costs.",
                mana_cost="{4}{R/G}", power=5.0)
GENERIC5 = card("Generic Five", "Artifact", "", mana_cost="{5}")


def test_nonland_permanent_that_enters_tapped():
    s = rigged(source=five_color(TIMELESS), land="Prism Land", lands_in_play=5, hand=["Timeless Lotus"])
    s.command_zone = []
    s = apply(s, {"cast": "Timeless Lotus"})
    assert available_mana(s) == 0


def test_colored_only_mana_cannot_pay_generic():
    assert effect_of(JEGANTHA, WUBRG).colored_only
    s = rigged(source=five_color(JEGANTHA, GENERIC5), land="Prism Land", lands_in_play=0,
               on_board=["Jegantha, the Wellspring"], hand=["Generic Five"])
    s.command_zone = []
    assert {"cast": "Generic Five"} not in legal_actions(s)


LANTERN = card("Chromatic Lantern", "Artifact", "Lands you control have \"{T}: Add one mana of any "
               "color.\"\n{T}: Add one mana of any color.", mana_cost="{3}")
ORRERY = card("Chromatic Orrery", "Legendary Artifact", "You may spend mana as though it were mana "
              "of any color.\n{T}: Add {C}{C}{C}{C}{C}.\n{5}, {T}: Draw a card for each color among "
              "permanents you control.", mana_cost="{7}")
BLUE = card("Blue Thing", "Artifact", "", mana_cost="{U}{U}")


def test_lantern_makes_lands_tap_for_any_color():
    d = ResolvedDeck(commanders=(commander(),), cards=((1, LANTERN), (1, BLUE), (97, forest())))
    s = rigged(source=d, lands_in_play=2, on_board=["Chromatic Lantern"], hand=["Blue Thing"])
    s.command_zone = []
    assert {"cast": "Blue Thing"} in legal_actions(s)


def test_orrery_lets_colorless_pay_colored_pips():
    d = ResolvedDeck(commanders=(commander(),), cards=((1, ORRERY), (1, BLUE), (97, forest())))
    s = rigged(source=d, lands_in_play=0, on_board=["Chromatic Orrery"], hand=["Blue Thing"])
    s.command_zone = []
    assert {"cast": "Blue Thing"} in legal_actions(s)


BLOOM = card("Bloom Tender", "Creature — Elf Druid", "Vivid — {T}: For each color among permanents "
             "you control, add one mana of that color.", mana_cost="{1}{G}", power=1.0)
RED = card("Red Rock", "Artifact", "", mana_cost="{R}", colors="R", identity="R")


def test_bloom_tender_taps_for_each_color_among_your_permanents():
    assert effect_of(BLOOM, WUBRG).mana_per_color
    s = rigged(source=five_color(BLOOM, RED), land="Prism Land", lands_in_play=0,
               on_board=["Bloom Tender", "Red Rock"])
    s.battlefield[0].entered = -1
    s.command_zone = []
    assert available_mana(s) == 2  # green (itself) and red


# --- Extra land drops and landfall ----------------------------------------------

DRYAD = card("Dryad of the Ilysian Grove", "Enchantment Creature — Nymph Dryad",
             "You may play an additional land on each of your turns.\nLands you control are every "
             "basic land type in addition to their other types.", mana_cost="{2}{G}", power=2.0)
ORACLE = card("Oracle of Mul Daya", "Creature — Elf Shaman", "You may play an additional land on each "
              "of your turns.\nPlay with the top card of your library revealed.\nYou may play lands "
              "from the top of your library.", mana_cost="{3}{G}", power=2.0)
COBRA = card("Lotus Cobra", "Creature — Snake", "Landfall — Whenever a land you control enters, add "
             "one mana of any color.", mana_cost="{1}{G}", power=2.0)
PROVISIONER = card("Tireless Provisioner", "Creature — Elf Scout", "Landfall — Whenever a land you "
                   "control enters, create a Food token or a Treasure token.", mana_cost="{2}{G}",
                   power=3.0)


def test_extra_land_drop():
    s = rigged(DRYAD, on_board=["Dryad of the Ilysian Grove"], hand=["Forest", "Forest", "Forest"])
    s = apply(apply(s, {"play_land": "Forest"}), {"play_land": "Forest"})
    assert not any("play_land" in a for a in legal_actions(s))


def test_oracle_plays_a_land_off_the_top():
    s = rigged(ORACLE, on_board=["Oracle of Mul Daya"])
    assert s.cards[s.library[0]].is_land
    assert {"play_land_top": "Forest"} in legal_actions(s)
    before = len(s.library)
    s = apply(s, {"play_land_top": "Forest"})
    assert len(s.library) == before - 1 and sum(p.is_land for p in s.battlefield) == 1


def test_landfall_mana_and_treasure():
    s = rigged(COBRA, PROVISIONER, on_board=["Lotus Cobra", "Tireless Provisioner"], hand=["Forest"])
    s = apply(s, {"play_land": "Forest"})
    assert (len(s.pool), s.treasures) == (1, 1)


# --- Cascade ---------------------------------------------------------------------

APEX = card("Apex Devastator", "Creature — Chimera Avatar", "Cascade, cascade, cascade, cascade "
            "(When you cast this spell, exile cards from the top of your library until you exile a "
            "nonland card that costs less. You may cast it without paying its mana cost. Put the "
            "exiled cards on the bottom in a random order. Then do it again for each cascade.)",
            mana_cost="{8}{G}{G}", power=10.0)
IMOTI = card("Imoti, Celebrant of Bounty", "Legendary Creature — Snake Druid", "Cascade (When you "
             "cast this spell, exile cards from the top of your library until you exile a nonland "
             "card that costs less. You may cast it without paying its mana cost. Put the exiled "
             "cards on the bottom in a random order.)\nSpells you cast with mana value 6 or greater "
             "have cascade.", mana_cost="{3}{G}{U}", power=6.0)


def test_cascade_counts_and_grants_parse():
    assert effect_of(APEX, WUBRG).cascade == 4
    assert effect_of(IMOTI, WUBRG).cascade == 1
    assert effect_of(IMOTI, WUBRG).grants_cascade_min == 6


def test_cascade_casts_cheaper_spells_free():
    bears = [card(f"Bear {i}", "Creature — Bear", "", mana_cost="{1}{G}", power=2.0) for i in range(4)]
    s = rigged(source=five_color(APEX, *bears), land="Prism Land", lands_in_play=10,
               hand=["Apex Devastator"])
    s.command_zone = []
    s.library = [i for i in s.library if s.cards[i].name.startswith("Bear")] + \
        [i for i in s.library if not s.cards[i].name.startswith("Bear")]
    s = apply(s, {"cast": "Apex Devastator"})
    on_board = [p.name for p in s.battlefield if p.name.startswith("Bear")]
    assert len(on_board) == 4
    assert available_mana(s) == 0  # only Apex itself was paid for


def test_cascade_skips_lands_and_costlier_cards_to_the_bottom():
    pricey = card("Pricey", "Creature — Giant", "", mana_cost="{9}{G}{G}", power=11.0)
    cheap = card("Cheap", "Creature — Elf", "", mana_cost="{G}", power=1.0)
    s = rigged(source=five_color(APEX, pricey, cheap), land="Prism Land", lands_in_play=10,
               hand=["Apex Devastator"])
    s.command_zone = []
    top = [i for i in s.library if s.cards[i].name in ("Pricey",)]
    lands = [i for i in s.library if s.cards[i].is_land][:2]
    rest = [i for i in s.library if i not in top + lands and s.cards[i].name != "Cheap"]
    cheap_idx = [i for i in s.library if s.cards[i].name == "Cheap"]
    s.library = top + lands + cheap_idx + rest
    s = apply(s, {"cast": "Apex Devastator"})
    assert "Cheap" in [p.name for p in s.battlefield]
    assert "Pricey" in [s.cards[i].name for i in s.library]
    assert "Pricey" not in [p.name for p in s.battlefield]


def test_imoti_gives_big_spells_cascade():
    cheap = card("Cheap", "Creature — Elf", "", mana_cost="{G}", power=1.0)
    s = rigged(source=five_color(IMOTI, BIG, cheap), land="Prism Land", lands_in_play=10,
               on_board=["Imoti, Celebrant of Bounty"], hand=["Big Spell"])
    s.command_zone = []
    s.library = [i for i in s.library if s.cards[i].name == "Cheap"] + \
        [i for i in s.library if s.cards[i].name != "Cheap"]
    s = apply(s, {"cast": "Big Spell"})
    assert "Cheap" in [p.name for p in s.battlefield]


# --- Approach of the Second Sun -------------------------------------------------

APPROACH = card("Approach of the Second Sun", "Sorcery", "If this spell was cast from your hand and "
                "you've cast another spell named Approach of the Second Sun this game, you win the "
                "game. Otherwise, put Approach of the Second Sun into its owner's library seventh "
                "from the top and you gain 7 life.", mana_cost="{1}", identity="W", colors="W")


def test_first_approach_goes_seventh_from_the_top_second_wins():
    s = rigged(source=five_color(APPROACH), land="Prism Land", lands_in_play=2, hand=["Approach of the Second Sun"])
    s.command_zone = []
    s = apply(s, {"cast": "Approach of the Second Sun"})
    assert s.cards[s.library[6]].name == "Approach of the Second Sun" and not s.over
    idx = s.library.pop(6)
    s.hand.append(idx)
    s = apply(apply(s, {"cast": "Approach of the Second Sun"}), PASS)
    assert s.over and s.win_by == "won:Approach of the Second Sun"


# --- Digging ----------------------------------------------------------------------

GENESIS = card("Genesis Ultimatum", "Sorcery", "Look at the top five cards of your library. Put any "
               "number of permanent cards from among them onto the battlefield and the rest into your "
               "hand. Exile Genesis Ultimatum.", mana_cost="{1}")
DIG = card("Dig Through Time", "Instant", "Delve (Each card you exile from your graveyard while "
           "casting this spell pays for {1}.)\nLook at the top seven cards of your library. Put two of "
           "them into your hand and the rest on the bottom of your library in any order.",
           mana_cost="{6}{U}{U}")


def test_genesis_puts_permanents_onto_the_battlefield_and_the_rest_in_hand():
    rock = card("Rock", "Artifact", "", mana_cost="{3}")
    spell = card("Spell", "Sorcery", "", mana_cost="{3}")
    s = rigged(source=five_color(GENESIS, rock, spell), land="Prism Land", lands_in_play=1,
               hand=["Genesis Ultimatum"])
    s.command_zone = []
    first = [i for i in s.library if s.cards[i].name in ("Rock", "Spell")]
    s.library = first + [i for i in s.library if i not in first]
    s = apply(s, {"cast": "Genesis Ultimatum"})
    assert "Rock" in [p.name for p in s.battlefield] and "Spell" in names(s, s.hand)
    assert sum(p.is_land for p in s.battlefield) == 1 + 3  # the three top lands too


def test_dig_through_time_takes_two_and_delves():
    effect = effect_of(DIG, frozenset("U"))
    assert (effect.dig_look, effect.dig_take, effect.delve) == (7, 2, True)
    s = rigged(source=five_color(DIG), land="Prism Land", lands_in_play=2, hand=["Dig Through Time"])
    s.command_zone = []
    s.graveyard = [s.library.pop() for _ in range(6)]
    hand_before = len(s.hand)
    s = apply(s, {"cast": "Dig Through Time"})  # six delved: pays {U}{U} with two lands
    assert len(s.hand) == hand_before - 1 + 2
    assert names(s, s.graveyard) == ["Dig Through Time"]


# --- Free spells ------------------------------------------------------------------

OWTM = card("One with the Multiverse", "Enchantment", "You may look at the top card of your library any "
            "time.\nYou may play lands and cast spells from the top of your library.\nOnce during each "
            "of your turns, you may cast a spell from your hand or the top of your library without "
            "paying its mana cost.", mana_cost="{6}{U}{U}")
EMERGENT = card("Emergent Ultimatum", "Sorcery", "Search your library for up to three monocolored cards "
                "with different names and exile them. An opponent chooses one of those cards. Shuffle "
                "that card into your library. You may cast the other cards without paying their mana "
                "costs. Exile Emergent Ultimatum.", mana_cost="{1}")


def test_one_with_the_multiverse_casts_one_big_spell_free_per_turn():
    s = rigged(source=five_color(OWTM, BIG, card("Big Two", "Sorcery", "", mana_cost="{9}{U}")),
               land="Prism Land", lands_in_play=1, on_board=["One with the Multiverse"],
               hand=["Big Spell", "Big Two"])
    s.command_zone = []
    s = apply(s, {"cast": "Big Spell"})
    assert {"cast": "Big Two"} not in legal_actions(s)  # once per turn
    s = apply(s, PASS)
    assert {"cast": "Big Two"} in legal_actions(s)


def test_emergent_ultimatum_casts_two_monocolored_cards_free():
    a = card("Green A", "Creature — Elf", "", mana_cost="{4}{G}", power=4.0, colors="G")
    b = card("Green B", "Creature — Elf", "", mana_cost="{4}{G}", power=4.0, colors="G")
    c = card("Green C", "Creature — Elf", "", mana_cost="{4}{G}", power=4.0, colors="G")
    s = rigged(source=five_color(EMERGENT, a, b, c), land="Prism Land", lands_in_play=1,
               hand=["Emergent Ultimatum"])
    s.command_zone = []
    s = apply(s, {"cast": "Emergent Ultimatum"})
    assert sum(p.name.startswith("Green") for p in s.battlefield) == 2
    assert sum(s.cards[i].name.startswith("Green") for i in s.library) == 1


# --- Library tricks ---------------------------------------------------------------

SYLVAN = card("Sylvan Library", "Enchantment", "At the beginning of your draw step, you may draw two "
              "additional cards. If you do, choose two cards in your hand drawn this turn. For each of "
              "those cards, pay 4 life or put the card on top of your library.", mana_cost="{1}{G}")
GROVE = card("Sterling Grove", "Enchantment", "Other enchantments you control have shroud.\n{1}, "
             "Sacrifice this enchantment: Search your library for an enchantment card, reveal it, then "
             "shuffle and put that card on top.", mana_cost="{G}{W}")
CONFLUX = card("Conflux", "Sorcery", "Search your library for a white card, a blue card, a black card, "
               "a red card, and a green card. Reveal those cards, put them into your hand, then "
               "shuffle.", mana_cost="{1}")


def test_sylvan_library_is_one_extra_card_a_turn():
    assert effect_of(SYLVAN, G).draw_per_turn == 1


def test_sterling_grove_puts_a_wanted_enchantment_on_top():
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Omniscience"}}
    s = rigged(source=five_color(GROVE, OMNISCIENCE), land="Prism Land", on_board=["Sterling Grove"],
               goal=goal)
    s.command_zone = []
    s = apply(s, PASS)
    assert "Omniscience" in names(s, s.hand)  # put on top, then drawn
    assert "Sterling Grove" in names(s, s.graveyard)


def test_conflux_fetches_five_cards():
    assert effect_of(CONFLUX, WUBRG).tutor_count == 5
    extras = [card(f"Card {i}", "Sorcery", "", mana_cost="{1}") for i in range(6)]
    s = rigged(source=five_color(CONFLUX, *extras), land="Prism Land", lands_in_play=1, hand=["Conflux"])
    s.command_zone = []
    s = apply(s, {"cast": "Conflux"})
    for _ in range(5):
        choice = next(a for a in legal_actions(s) if "tutor" in a)
        s = apply(s, choice)
    assert s.pending_tutor is None and len(s.hand) == 5


# --- Opponents' draws ---------------------------------------------------------------

SPHINX = card("Consecrated Sphinx", "Creature — Sphinx", "Flying\nWhenever an opponent draws a card, "
              "you may draw two cards.", mana_cost="{4}{U}{U}", power=4.0)
TITHE = card("Smothering Tithe", "Enchantment", "Whenever an opponent draws a card, that player may pay "
             "{2}. If the player doesn't, you create a Treasure token.", mana_cost="{3}{W}")


def test_sphinx_draws_six_a_round_and_stops_near_decking():
    from mtgpt.goldfish.engine import SPHINX_LIBRARY_FLOOR
    s = rigged(SPHINX, on_board=["Consecrated Sphinx"])
    s.command_zone = []
    before = len(s.hand)
    s = apply(s, PASS)
    assert len(s.hand) == before + 6 + 1  # three opponents' draws, then our draw step
    s.library = s.library[:SPHINX_LIBRARY_FLOOR + 1]
    s = apply(s, PASS)
    assert len(s.library) >= SPHINX_LIBRARY_FLOOR - 2 and not s.over


def test_tithe_makes_one_treasure_a_round():
    s = rigged(TITHE, on_board=["Smothering Tithe"])
    s.command_zone = []
    s = apply(s, PASS)
    assert s.treasures == 1


def test_new_mechanic_state_round_trips():
    s = rigged(ORACLE, DRYAD, on_board=["Oracle of Mul Daya", "Dryad of the Ilysian Grove"],
               hand=["Forest"])
    s = apply(s, {"play_land": "Forest"})
    data = to_dict(s)
    assert to_dict(from_dict(json.loads(json.dumps(data)))) == data
