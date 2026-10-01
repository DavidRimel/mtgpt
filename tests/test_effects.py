from mtgpt.effects import effect_from_dict, effect_of, effect_to_dict, is_unmodeled

from simdeck import (COUNTERSPELL, DEMONIC_TUTOR, LLANOWAR, NIGHTS_WHISPER,
                     RAMPANT_GROWTH, SOL_RING, SWORDS, TEFERIS_PROTECTION, card, forest)

G = frozenset("G")
BG = frozenset("BG")


def test_sol_ring_makes_two_colorless():
    effect = effect_of(SOL_RING, G)
    assert (effect.mana, effect.mana_colors) == (2, frozenset("C"))
    assert effect.is_ramp


def test_dork_makes_one_green():
    effect = effect_of(LLANOWAR, G)
    assert (effect.mana, effect.mana_colors, effect.power) == (1, G, 1.0)


def test_any_color_is_bounded_by_identity():
    birds = card("Birds of Paradise", "Creature — Bird",
                 "Flying\n{T}: Add one mana of any color.", mana_cost="{G}", power=0.0)
    assert effect_of(birds, BG).mana_colors == BG


def test_either_color_is_one_mana():
    talisman = card("Talisman", "Artifact", "{T}: Add {B} or {G}.", mana_cost="{2}")
    assert (effect_of(talisman, BG).mana, effect_of(talisman, BG).mana_colors) == (1, BG)


def test_talisman_multiple_alternatives():
    # Multiple {T}: Add lines are alternatives (tapping is the cost), so mana = max
    # and mana_colors = union of all colors across lines
    talisman = card("Talisman", "Artifact",
                    "{T}: Add {C}.\n{T}: Add {B} or {G}. This artifact deals 1 damage to you.",
                    mana_cost="{2}")
    effect = effect_of(talisman, BG)
    assert effect.mana == 1
    assert effect.mana_colors == frozenset("BCG")


def test_mana_filter_counts_its_net_mana():
    # {1}, {T}: Add {B}{B} produces 2 but costs 1, net = 1
    filt = card("Filter", "Artifact", "{1}, {T}: Add {B}{B}.", mana_cost="{2}")
    effect = effect_of(filt, BG)
    assert (effect.mana, effect.mana_colors) == (1, frozenset("B"))
    # A Signet produces net mana and is thus modeled
    signet = card("Signet", "Artifact", "{1}, {T}: Add {B}{G}.", mana_cost="{2}")
    effect = effect_of(signet, BG)
    assert (effect.mana, effect.mana_colors) == (1, BG)
    assert not is_unmodeled(signet, effect)


def test_ritual_is_one_shot_mana():
    ritual = card("Dark Ritual", "Instant", "Add {B}{B}{B}.", mana_cost="{B}", identity="B")
    effect = effect_of(ritual, frozenset("B"))
    assert (effect.mana, effect.mana_once) == (0, 3)


def test_treasure_on_enter_is_counted():
    maker = card("Maker", "Creature — Goblin",
                 "When this creature enters, create a Treasure token.", power=1.0)
    assert effect_of(maker, G).treasure_once == 1


def test_whenever_treasure_is_skipped():
    tithe = card("Smothering Tithe", "Enchantment",
                 "Whenever an opponent draws a card, that player may pay {2}. If the "
                 "player doesn't, you create a Treasure token.")
    effect = effect_of(tithe, G)
    assert effect.treasure_once == 0
    assert is_unmodeled(tithe, effect)


def test_rampant_growth_fetches_one_tapped():
    effect = effect_of(RAMPANT_GROWTH, G)
    assert (effect.fetch_battlefield, effect.fetch_hand, effect.fetch_tapped) == (1, 0, True)
    assert effect.tutor is None


def test_cultivate_fetches_one_to_battlefield_one_to_hand():
    cultivate = card(
        "Cultivate", "Sorcery",
        "Search your library for up to two basic land cards, reveal those cards, put "
        "one onto the battlefield tapped and the other into your hand, then shuffle.")
    effect = effect_of(cultivate, G)
    assert (effect.fetch_battlefield, effect.fetch_hand, effect.fetch_tapped) == (1, 1, True)


def test_explosive_vegetation_fetches_two():
    veg = card("Explosive Vegetation", "Sorcery",
               "Search your library for up to two basic land cards, put them onto the "
               "battlefield tapped, then shuffle.")
    assert effect_of(veg, G).fetch_battlefield == 2


def test_draw_spell_and_upkeep_draw():
    arena = card("Phyrexian Arena", "Enchantment",
                 "At the beginning of your upkeep, you draw a card and you lose 1 life.")
    assert effect_of(NIGHTS_WHISPER, G).draw_once == 2
    assert effect_of(arena, G).draw_per_turn == 1


def test_activated_draw_is_skipped():
    loot = card("Loot", "Artifact", "{2}, {T}: Draw a card.")
    assert effect_of(loot, G).draw_once == 0


def test_tutor_restrictions():
    worldly = card("Worldly Tutor", "Instant",
                   "Search your library for a creature card, reveal it, then shuffle "
                   "and put the card on top.")
    mystical = card("Mystical Tutor", "Instant",
                    "Search your library for an instant or sorcery card, reveal it, then "
                    "shuffle and put that card on top.")
    assert effect_of(DEMONIC_TUTOR, G).tutor == "any"
    assert effect_of(worldly, G).tutor == "creature"
    assert effect_of(mystical, G).tutor == "instant|sorcery"


def test_tutor_strips_non_prefix():
    # "noncreature, nonland" should strip "non" and find "card" → "any"
    tutor = card("Tutor", "Instant",
                 "Search your library for a noncreature, nonland card, reveal it, "
                 "put it into your hand, then shuffle.")
    assert effect_of(tutor, G).tutor == "any"


def test_lands():
    tower = card("Command Tower", "Land",
                 "{T}: Add one mana of any color in your commander's color identity.",
                 produced_mana="WUBRG")
    gate = card("Gate", "Land — Gate", "This land enters tapped.\n{T}: Add {B} or {G}.",
                produced_mana="BG")
    checkland = card("Check", "Land",
                     "This land enters tapped unless you control a Forest.\n{T}: Add {B} or {G}.",
                     produced_mana="BG")
    wilds = card("Evolving Wilds", "Land",
                 "{T}, Sacrifice this land: Search your library for a basic land card, "
                 "put it onto the battlefield tapped, then shuffle.")
    assert effect_of(forest(), G).land_colors == G
    assert effect_of(tower, BG).land_colors == BG
    assert effect_of(gate, BG).enters_tapped
    assert not effect_of(checkland, BG).enters_tapped
    assert effect_of(wilds, BG).land_colors == BG
    assert effect_of(wilds, BG).enters_tapped
    # Shockland is optimistic: assume life payment is made, so untapped
    shock = card("Shock", "Land — Island Swamp",
                 "As this land enters the battlefield, you may pay 2 life. If you don't, "
                 "it enters tapped.",
                 produced_mana="UB")
    assert not effect_of(shock, BG).enters_tapped


def test_mdfc_land_face_colors():
    mdfc = card("Spell // Land", "Sorcery // Land", "Draw two cards.", produced_mana="G")
    effect = effect_of(mdfc, G)
    assert effect.land_colors == G
    assert effect.draw_once == 2


def test_equipment_power_bonus():
    splitter = card("Bonesplitter", "Artifact — Equipment",
                    "Equipped creature gets +2/+0.\nEquip {1}", mana_cost="{1}")
    assert effect_of(splitter, G).power_bonus == 2


def test_interaction_is_held():
    assert effect_of(SWORDS, G).held == frozenset({"removal"})
    assert effect_of(COUNTERSPELL, G).held == frozenset({"counterspell"})
    teferi = effect_of(TEFERIS_PROTECTION, G)
    assert "protection" in teferi.held and teferi.wipe_proof


def test_unmodeled_ignores_keyword_only_creatures():
    vanilla = card("Serra Angel", "Creature — Angel", "Flying, vigilance", power=4.0)
    atraxa = card("Atraxa", "Legendary Creature — Angel",
                  "Flying\nAt the beginning of your end step, proliferate.", power=4.0)
    assert not is_unmodeled(vanilla, effect_of(vanilla, G))
    assert is_unmodeled(atraxa, effect_of(atraxa, G))
    assert not is_unmodeled(forest(), effect_of(forest(), G))


def test_effect_round_trips_through_dict():
    effect = effect_of(TEFERIS_PROTECTION, G)
    assert effect_from_dict(effect_to_dict(effect)) == effect
