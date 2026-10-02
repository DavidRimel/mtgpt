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


# --- Opponents' win attempts (fast tables) -------------------------------------

WINNOWER = card("Void Winnower", "Creature — Eldrazi", "Your opponents can't cast spells with even "
                "mana values. (Zero is even.)\nYour opponents can't block with creatures with even mana "
                "values.", mana_cost="{9}", power=11.0)
FAST_TABLE = {"archetype": "custom", "thing": "commander", "win": NEVER,
              "opponent_win": {"from_turn": 2, "answers": ["removal", "counterspell", "stax"]}}


def test_stax_is_parsed():
    assert effect_of(WINNOWER, WUBRG).stax


def test_an_unanswered_win_attempt_loses():
    s = rigged(lands_in_play=1, goal=FAST_TABLE)
    s.command_zone = []
    s = apply(s, PASS)
    assert s.over and s.loss_by == "opponent_win" and s.checkpoints["loss"] == 2


def test_removal_in_hand_answers_and_is_used_up():
    from simdeck import SWORDS
    s = rigged(SWORDS, hand=["Swords to Plowshares"], lands_in_play=1, goal=FAST_TABLE)
    s.command_zone = []
    s = apply(s, PASS)
    assert not s.over and "Swords to Plowshares" in names(s, s.graveyard)
    assert s.win_attempts[-1] == {"turn": 2, "stopped": True, "by": "Swords to Plowshares"}
    s = apply(s, PASS)  # round 3: they try again, nothing left
    assert s.loss_by == "opponent_win"


def test_a_stax_piece_on_the_battlefield_stops_every_attempt():
    s = rigged(WINNOWER, on_board=["Void Winnower"], lands_in_play=1, goal=FAST_TABLE)
    s.command_zone = []
    for _ in range(3):
        s = apply(s, PASS)
    assert not s.over and all(a["by"] == "Void Winnower" for a in s.win_attempts)


def test_protection_does_not_answer_a_win_attempt():
    from simdeck import TEFERIS_PROTECTION
    s = rigged(TEFERIS_PROTECTION, hand=["Teferi's Protection"], lands_in_play=1, goal=FAST_TABLE)
    s.command_zone = []
    s = apply(s, PASS)
    assert s.loss_by == "opponent_win"


def test_answer_kinds_are_configurable():
    from simdeck import COUNTERSPELL
    goal = {**FAST_TABLE, "opponent_win": {"from_turn": 2, "answers": ["removal"]}}
    s = rigged(COUNTERSPELL, hand=["Counterspell"], lands_in_play=1, goal=goal)
    s.command_zone = []
    s = apply(s, PASS)
    assert s.loss_by == "opponent_win"


def test_report_has_opponent_win_and_win_by_round():
    from mtgpt.goldfish.run import simulate
    from simdeck import deck
    r = simulate(deck(), {**FAST_TABLE, "opponent_win": {"from_turn": 5}}, games=10)
    assert r["opponent_win"]["attempts"] == 10 and r["opponent_win"]["answered_rate"] == 0.0
    assert r["loss"]["by_reason"] == {"opponent_win": 1.0}
    assert r["win"]["win_by_round"] == [0.0] * 10


# --- Fast mana and free interaction (bracket 4) -------------------------------

VAULT = card("Mana Vault", "Artifact", "This artifact doesn't untap during your untap step.\nAt the "
             "beginning of your upkeep, you may pay {4}. If you do, untap this artifact.\nAt the beginning "
             "of your draw step, if this artifact is tapped, it deals 1 damage to you.\n{T}: Add {C}{C}{C}.",
             mana_cost="{1}", colors="")
PACT = card("Pact of Negation", "Instant", "Counter target spell.\nAt the beginning of your next upkeep, "
            "pay {3}{U}{U}. If you don't, you lose the game.", mana_cost="{0}", colors="U")
VAMPIRIC = card("Vampiric Tutor", "Instant", "Search your library for a card, then shuffle and put that "
                "card on top. You lose 2 life.", mana_cost="{B}", colors="B")
DIAMOND = card("Mox Diamond", "Artifact", "If this artifact would enter, you may discard a land card "
               "instead. If you do, put this artifact onto the battlefield. If you don't, put it into its "
               "owner's graveyard.\n{T}: Add one mana of any color.", mana_cost="{0}", colors="")
CHROME = card("Chrome Mox", "Artifact", "Imprint — When this artifact enters, you may exile a nonartifact, "
              "nonland card from your hand.\n{T}: Add one mana of any of the exiled card's colors.",
              mana_cost="{0}", colors="")
RED_SPELL = card("Red Spell", "Sorcery", "", mana_cost="{3}{R}", colors="R", identity="R")


def test_mana_vault_does_not_untap():
    assert effect_of(VAULT, WUBRG).no_untap
    s = rigged(source=five_color(VAULT), land="Prism Land", on_board=["Mana Vault"])
    s.command_zone = []
    assert available_mana(s) == 3
    s.battlefield[0].tapped = True
    s = apply(s, PASS)
    assert available_mana(s) == 0  # still tapped next turn


def test_a_spent_pact_must_be_paid_next_upkeep_or_you_lose():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "opponent_win": {"from_turn": 2}}
    s = rigged(source=five_color(PACT), land="Prism Land", hand=["Pact of Negation"], lands_in_play=2,
               goal=goal)
    s.command_zone = []
    s = apply(s, PASS)  # round 2: Pact answers the attempt; the upkeep trigger is due now
    assert s.loss_by == "pact"  # two lands cannot pay {3}{U}{U}
    s = rigged(source=five_color(PACT), land="Prism Land", hand=["Pact of Negation"], lands_in_play=5,
               goal=goal)
    s.command_zone = []
    s = apply(s, PASS)
    assert not s.over and available_mana(s) == 0  # paid with all five lands


def test_vampiric_tutor_puts_the_card_on_top():
    assert effect_of(VAMPIRIC, WUBRG).tutor_to_top
    s = rigged(source=five_color(VAMPIRIC, BIG), land="Prism Land", hand=["Vampiric Tutor"],
               lands_in_play=1)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Vampiric Tutor"}), {"tutor": "Big Spell"})
    assert s.cards[s.library[0]].name == "Big Spell" and "Big Spell" not in names(s, s.hand)


def test_mox_diamond_needs_a_land_to_discard():
    assert effect_of(DIAMOND, WUBRG).discard_land
    s = rigged(source=five_color(DIAMOND), land="Prism Land", hand=["Mox Diamond"])
    s.command_zone = []
    assert {"cast": "Mox Diamond"} not in legal_actions(s)
    s = rigged(source=five_color(DIAMOND), land="Prism Land", hand=["Mox Diamond", "Prism Land"])
    s.command_zone = []
    s = apply(s, {"cast": "Mox Diamond"})
    assert "Prism Land" in names(s, s.graveyard) and available_mana(s) == 1


def test_chrome_mox_imprints_a_card_and_taps_for_its_colors():
    assert effect_of(CHROME, WUBRG).imprint
    s = rigged(source=five_color(CHROME, RED_SPELL), land="Prism Land", hand=["Chrome Mox", "Red Spell"])
    s.command_zone = []
    s = apply(s, {"cast": "Chrome Mox"})
    assert "Red Spell" not in names(s, s.hand)
    assert [sorted(u.colors) for u in __import__("mtgpt.goldfish.engine", fromlist=["_units"])._units(s)] == [["R"]]
    s2 = rigged(source=five_color(CHROME), land="Prism Land", hand=["Chrome Mox"])
    s2.command_zone = []
    s2 = apply(s2, {"cast": "Chrome Mox"})
    assert available_mana(s2) == 0  # nothing to imprint


def test_disruption_spends_pure_protection_before_a_win_attempt_answer():
    from simdeck import TEFERIS_PROTECTION
    charm = card("Boros Charm", "Instant", "Choose one —\n• Boros Charm deals 4 damage to target player or "
                 "planeswalker.\n• Permanents you control gain indestructible until end of turn.\n• Target "
                 "creature gains double strike until end of turn.", mana_cost="{R}{W}")
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"commander_removal": 1.0, "from_turn": 1}, "opponent_win": {"from_turn": 9}}
    s = rigged(TEFERIS_PROTECTION, charm, hand=["Boros Charm", "Teferi's Protection"], lands_in_play=1,
               commander_out=True, goal=goal)
    s = apply(s, PASS)
    assert "Teferi's Protection" in names(s, s.graveyard) and "Boros Charm" in names(s, s.hand)


def test_imprint_never_takes_an_answer():
    from simdeck import COUNTERSPELL
    green = card("Green Thing", "Creature — Elf", "", mana_cost="{3}{G}", power=3.0, colors="G")
    s = rigged(source=five_color(CHROME, COUNTERSPELL, green), land="Prism Land",
               hand=["Chrome Mox", "Counterspell", "Green Thing"])
    s.command_zone = []
    s = apply(s, {"cast": "Chrome Mox"})
    assert "Counterspell" in names(s, s.hand) and "Green Thing" not in names(s, s.hand)


def test_a_wipe_spares_an_imprinted_mox():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "disruption": {"board_wipe": 1.0, "from_turn": 1}}
    s = rigged(source=five_color(CHROME, RED_SPELL), land="Prism Land", hand=["Chrome Mox", "Red Spell"],
               goal=goal)
    s.command_zone = []
    s = apply(apply(s, {"cast": "Chrome Mox"}), PASS)
    assert "Chrome Mox" in [p.name for p in s.battlefield]


# --- Thassa's Oracle lines ---------------------------------------------------

ORACLE_T = card("Thassa's Oracle", "Creature — Merfolk Wizard", "When Thassa's Oracle enters, look at the "
                "top X cards of your library, where X is your devotion to blue. Put up to one of them on top "
                "of your library and the rest on the bottom of your library in a random order. If X is "
                "greater than or equal to the number of cards in your library, you win the game.",
                mana_cost="{U}{U}", power=1.0, colors="U")
CONSULT = card("Demonic Consultation", "Instant", "Choose a card name. Exile the top six cards of your "
               "library, then reveal cards from the top of your library until you reveal a card with the "
               "chosen name. Put that card into your hand and exile all other cards revealed this way.",
               mana_cost="{B}", colors="B")
TAINTED = card("Tainted Pact", "Instant", "Exile the top card of your library. You may put that card into "
               "your hand unless it has the same name as another card exiled this way. Repeat this process "
               "until you put a card into your hand or you exile two cards with the same name, whichever "
               "comes first.", mana_cost="{1}{B}", colors="B")


def test_oracle_and_consultation_parse():
    assert effect_of(ORACLE_T, WUBRG).thoracle
    assert effect_of(CONSULT, WUBRG).exile_library
    assert effect_of(TAINTED, WUBRG).exile_library


def test_consultation_then_oracle_wins():
    s = rigged(source=five_color(ORACLE_T, CONSULT), land="Prism Land", lands_in_play=3,
               hand=["Thassa's Oracle", "Demonic Consultation"])
    s.command_zone = []
    from mtgpt.goldfish.policy import choose
    s = apply(s, choose(s))  # Consultation: the look-ahead sees the win
    assert s.library == []
    s = apply(s, choose(s))  # Oracle
    s = apply(s, {"pass": True})
    assert s.over and s.win_by == "won:Thassa's Oracle"


def test_oracle_and_consultation_are_held_with_a_full_library():
    from mtgpt.goldfish.policy import choose
    s = rigged(source=five_color(ORACLE_T, CONSULT), land="Prism Land", lands_in_play=1,
               hand=["Thassa's Oracle", "Demonic Consultation"])
    s.command_zone = []
    assert choose(s) == {"pass": True}  # one mana: Consultation alone would only exile the library


def test_pact_is_the_last_answer_spent():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER, "opponent_win": {"from_turn": 2}}
    from simdeck import COUNTERSPELL
    s = rigged(source=five_color(PACT, COUNTERSPELL), land="Prism Land", lands_in_play=1,
               hand=["Pact of Negation", "Counterspell"], goal=goal)
    s.command_zone = []
    s = apply(s, PASS)
    assert s.win_attempts[-1]["by"] == "Counterspell" and "Pact of Negation" in names(s, s.hand)


def test_win_attempts_can_come_every_two_or_three_rounds():
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "opponent_win": {"from_turn": 5, "every": [2, 3]}}
    from mtgpt.goldfish.engine import new_game, prepare
    from simdeck import deck
    setup = prepare(deck(), goal)
    schedules = {tuple(new_game(setup, seed=f"1-{i}", turn_cap=20).attempt_rounds) for i in range(30)}
    for rounds in schedules:
        assert rounds[0] == 5
        assert all(b - a in (2, 3) for a, b in zip(rounds, rounds[1:]))
    assert len(schedules) > 1  # the gaps vary from game to game


def test_no_attempt_between_scheduled_rounds():
    from simdeck import SWORDS
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "opponent_win": {"from_turn": 2, "every": [3, 3]}}
    s = rigged(SWORDS, hand=["Swords to Plowshares"], lands_in_play=1, goal=goal)
    s.command_zone = []
    assert s.attempt_rounds[:2] == [2, 5]
    for _ in range(3):
        s = apply(s, PASS)  # rounds 2 (answered), 3, 4: no attempt on 3 or 4
    assert not s.over and len(s.win_attempts) == 1


# --- Repeatable removal engines answer win attempts -----------------------------

def test_a_removal_engine_on_the_battlefield_answers_every_attempt_and_stays():
    from simdeck import BEAR
    goal = {**FAST_TABLE, "opponent_win": {"from_turn": 2, "answers": ["removal"]},
            "engine": {"Grizzly Bears": {"removal_engine": True}}}
    s = rigged(BEAR, on_board=["Grizzly Bears"], lands_in_play=1, goal=goal)
    s.command_zone = []
    for _ in range(3):
        s = apply(s, PASS)
    assert not s.over and all(a["by"] == "Grizzly Bears" for a in s.win_attempts)


def test_a_removal_engine_does_not_answer_when_removal_is_not_an_answer():
    from simdeck import BEAR
    goal = {**FAST_TABLE, "opponent_win": {"from_turn": 2, "answers": ["counterspell"]},
            "engine": {"Grizzly Bears": {"removal_engine": True}}}
    s = rigged(BEAR, on_board=["Grizzly Bears"], lands_in_play=1, goal=goal)
    s.command_zone = []
    s = apply(s, PASS)
    assert s.loss_by == "opponent_win"


def test_a_removal_engine_counts_as_standing_removal():
    from mtgpt.goldfish.engine import held_counts
    from simdeck import BEAR
    goal = {**FAST_TABLE, "engine": {"Grizzly Bears": {"removal_engine": True}}}
    s = rigged(BEAR, on_board=["Grizzly Bears"], lands_in_play=1, goal=goal)
    assert held_counts(s)["removal"] == 1


def test_worldly_tutor_puts_the_card_on_top():
    worldly = card("Worldly Tutor", "Instant", "Search your library for a creature card, reveal it, then "
                   "shuffle and put the card on top.", mana_cost="{G}")
    assert effect_of(worldly, WUBRG).tutor_to_top


# --- X tutors that put the card onto the battlefield (Chord, GSZ, Finale) --------

GSZ = card("Green Sun's Zenith", "Sorcery", "Search your library for a green creature card with mana value "
           "X or less, put it onto the battlefield, then shuffle. Shuffle Green Sun's Zenith into its "
           "owner's library.", mana_cost="{X}{G}")
FINALE = card("Finale of Devastation", "Sorcery", "Search your library and/or graveyard for a creature card "
              "with mana value X or less and put it onto the battlefield. If you search your library this "
              "way, shuffle. If X is 10 or more, creatures you control get +X/+X and gain haste until end "
              "of turn.", mana_cost="{X}{G}{G}")
GREEN_3 = card("Green Three", "Creature — Elf", "", mana_cost="{2}{G}", power=3.0, identity="G")
BLACK_2 = card("Black Two", "Creature — Zombie", "", mana_cost="{1}{B}", power=2.0, identity="B")


def test_x_tutor_effects_parse():
    gsz = effect_of(GSZ, WUBRG)
    assert gsz.tutor == "creature" and gsz.tutor_x and gsz.tutor_battlefield and gsz.tutor_color == "G"
    finale = effect_of(FINALE, WUBRG)
    assert finale.tutor == "creature" and finale.tutor_x and finale.tutor_battlefield
    assert finale.tutor_color is None


def test_x_tutor_offers_only_what_the_mana_left_can_pay_for_and_puts_it_onto_the_battlefield():
    s = rigged(source=five_color(FINALE, GREEN_3, BLACK_2), land="Prism Land",
               hand=["Finale of Devastation"], lands_in_play=4)
    s.command_zone = []
    s = apply(s, {"cast": "Finale of Devastation"})  # 2 paid, 2 left: X can be 2
    offered = {a["tutor"] for a in legal_actions(s)}
    assert offered == {"Black Two"}
    s = apply(s, {"tutor": "Black Two"})
    assert "Black Two" in names(s, [p.card for p in s.battlefield]) and available_mana(s) == 0


def test_green_suns_zenith_finds_only_green_creatures():
    s = rigged(source=five_color(GSZ, GREEN_3, BLACK_2), land="Prism Land",
               hand=["Green Sun's Zenith"], lands_in_play=5)
    s.command_zone = []
    s = apply(s, {"cast": "Green Sun's Zenith"})
    assert {a["tutor"] for a in legal_actions(s)} == {"Green Three"}


def test_x_tutor_is_not_castable_when_nothing_is_affordable():
    s = rigged(source=five_color(FINALE, GREEN_3), land="Prism Land",
               hand=["Finale of Devastation"], lands_in_play=2)
    s.command_zone = []
    assert {"cast": "Finale of Devastation"} not in legal_actions(s)
