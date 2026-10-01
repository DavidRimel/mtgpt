import pytest

from mtgpt.classify import classify, classify_deck
from mtgpt.models import Card, Function, ResolvedDeck

F = Function


def card(name, type_line, oracle_text, **kw):
    defaults = dict(
        name=name,
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost="{1}{G}",
        color_identity=frozenset("G"),
        colors=frozenset("G"),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


def test_basic_land_is_land_only():
    forest = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)",
                  produced_mana=frozenset("G"))
    assert classify(forest) == frozenset({F.LAND})


def test_utility_land_is_land():
    tower = card("Command Tower", "Land",
                 "{T}: Add one mana of any color in your commander's color identity.",
                 produced_mana=frozenset("WUBRG"))
    assert F.LAND in classify(tower)


def test_mana_rock_is_ramp():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.",
               produced_mana=frozenset("C"))
    assert F.RAMP in classify(sol)
    assert F.LAND not in classify(sol)


def test_mana_dork_is_ramp():
    birds = card("Birds of Paradise", "Creature — Bird",
                 "Flying\n{T}: Add one mana of any color.",
                 produced_mana=frozenset("WUBRG"))
    assert F.RAMP in classify(birds)


def test_treasure_maker_is_ramp():
    dockside = card(
        "Dockside Extortionist", "Creature — Goblin Pirate",
        "When this creature enters, create X Treasure tokens, where X is the "
        "number of artifacts and enchantments your opponents control.",
        produced_mana=frozenset("WUBRG"),
    )
    assert F.RAMP in classify(dockside)


def test_produced_mana_alone_does_not_make_ramp():
    """A creature that makes no mana must not be ramp just because Scryfall
    lists produced_mana for an unrelated reason."""
    decoy = card("Decoy", "Creature — Human", "Flying",
                 produced_mana=frozenset("WUBRG"))
    assert F.RAMP not in classify(decoy)


def test_land_ramp_spell_is_ramp_but_not_tutor():
    cultivate = card(
        "Cultivate", "Sorcery",
        "Search your library for up to two basic land cards, reveal those cards, "
        "put one onto the battlefield tapped and the other into your hand, then shuffle.",
    )
    tags = classify(cultivate)
    assert F.RAMP in tags
    assert F.TUTOR not in tags


def test_unrestricted_search_is_a_tutor():
    demonic = card("Demonic Tutor", "Sorcery",
                   "Search your library for a card, put that card into your hand, "
                   "then shuffle.")
    assert F.TUTOR in classify(demonic)


def test_spot_removal_requires_a_target():
    stp = card("Swords to Plowshares", "Instant",
               "Exile target creature. Its controller gains life equal to its power.")
    tags = classify(stp)
    assert F.SPOT_REMOVAL in tags
    assert F.SWEEPER not in tags


def test_sweeper_hits_all_creatures():
    wrath = card("Wrath of God", "Sorcery",
                 "Destroy all creatures. They can't be regenerated.")
    tags = classify(wrath)
    assert F.SWEEPER in tags
    assert F.SPOT_REMOVAL not in tags


def test_mass_land_denial_is_not_counted_as_a_sweeper():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    tags = classify(armageddon)
    assert F.MASS_LAND_DENIAL in tags
    assert F.SWEEPER not in tags


@pytest.mark.parametrize(
    "name,oracle,is_mld,is_sweeper",
    [
        ("Armageddon", "Destroy all lands.", True, False),
        ("Ravages of War", "Destroy all lands.", True, False),
        # "lands" is not adjacent to "all" on the classic MLD cards.
        ("Jokulhaups",
         "Destroy all artifacts, creatures, and lands. They can't be regenerated.",
         True, True),
        ("Devastation", "Destroy all creatures and lands.", True, True),
        ("Wrath of God", "Destroy all creatures. They can't be regenerated.", False, True),
        # "nonland" must not register as a land.
        ("Nonland wipe", "Destroy all nonland permanents.", False, True),
        ("Cyclonic Rift overload",
         "Return all nonland permanents you don't control to their owners' hands.",
         False, False),
    ],
    ids=["armageddon", "ravages", "jokulhaups", "devastation", "wrath", "nonland", "rift"],
)
def test_mass_land_denial_detection(name, oracle, is_mld, is_sweeper):
    tags = classify(card(name, "Sorcery", oracle))
    assert (F.MASS_LAND_DENIAL in tags) is is_mld
    assert (F.SWEEPER in tags) is is_sweeper


@pytest.mark.parametrize(
    "name,oracle",
    [
        ("Time Warp", "Target player takes an extra turn after this one."),
        ("Nexus of Fate", "Take an extra turn after this one."),
        # Plural: "takes two extra turns".
        ("Time Stretch", "Target player takes two extra turns after this one."),
    ],
    ids=["time-warp", "nexus", "time-stretch"],
)
def test_extra_turn_spells_detected(name, oracle):
    assert F.EXTRA_TURNS in classify(card(name, "Sorcery", oracle))


def test_counterspell():
    cs = card("Counterspell", "Instant", "Counter target spell.")
    assert F.COUNTERSPELL in classify(cs)


def test_card_draw():
    div = card("Divination", "Sorcery", "Draw two cards.")
    assert F.DRAW in classify(div)


def test_cantrip_draw_a_card():
    ancestral = card("Ancestral Recall", "Instant", "Target player draws three cards.")
    assert F.DRAW in classify(ancestral)


def test_protection_effects():
    teferi = card("Teferi's Protection", "Instant",
                  "Until your next turn, your life total can't change and you gain "
                  "protection from everything. Phase out all permanents you control.")
    assert F.PROTECTION in classify(teferi)


def test_extra_turns():
    time_warp = card("Time Warp", "Sorcery", "Target player takes an extra turn after this one.")
    assert F.EXTRA_TURNS in classify(time_warp)


def test_recursion():
    regrowth = card("Regrowth", "Sorcery",
                    "Return target card from your graveyard to your hand.")
    assert F.RECURSION in classify(regrowth)


def test_explicit_wincon():
    lab_man = card("Laboratory Maniac", "Creature — Human Wizard",
                   "If you would draw a card while your library has no cards in it, "
                   "you win the game instead.")
    assert F.WINCON in classify(lab_man)


def test_cards_can_carry_several_functions():
    """Cultivate fetches land and shuffles: ramp, and not a bare SYNERGY tag."""
    cultivate = card(
        "Cultivate", "Sorcery",
        "Search your library for up to two basic land cards, reveal those cards, "
        "put one onto the battlefield tapped and the other into your hand, then shuffle.",
    )
    tags = classify(cultivate)
    assert F.RAMP in tags
    assert F.SYNERGY not in tags

    beast_within = card("Beast Within", "Instant",
                        "Destroy target permanent. Its controller creates a 3/3 green "
                        "Beast creature token.")
    beast_tags = classify(beast_within)
    assert F.SPOT_REMOVAL in beast_tags
    assert F.SYNERGY not in beast_tags


def test_uncategorized_card_falls_back_to_synergy():
    pet = card("Weird Pet Card", "Creature — Bear", "Trample")
    assert classify(pet) == frozenset({F.SYNERGY})


def test_synergy_is_not_added_alongside_real_tags():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", produced_mana=frozenset("C"))
    assert F.SYNERGY not in classify(sol)


def test_mdfc_land_is_not_tagged_land():
    agadeem = card(
        "Agadeem's Awakening // Agadeem, the Undercrypt", "Sorcery // Land",
        "Return from your graveyard to the battlefield any number of target "
        "creature cards that each have a different mana value X or less.",
        layout="modal_dfc", produced_mana=frozenset("B"),
    )
    tags = classify(agadeem)
    assert F.LAND not in tags
    assert F.RECURSION in tags


# Real oracle text from Scryfall, 2026-09-30. Each of these was misclassified by
# an earlier draft of the regexes above; they are the reason those regexes look
# the way they do. Keep them.
REAL_STAPLES = [
    # (name, type_line, oracle_text, must_include, must_exclude)
    ("Nature's Lore", "Sorcery",
     "Search your library for a Forest card, put that card onto the battlefield, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Three Visits", "Sorcery",
     "Search your library for a Forest card, put it onto the battlefield, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Farseek", "Sorcery",
     "Search your library for a Plains, Island, Swamp, or Mountain card, put it onto "
     "the battlefield tapped, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Swan Song", "Instant",
     "Counter target enchantment, instant, or sorcery spell. Its controller creates a "
     "2/2 blue Bird creature token with flying.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Dovin's Veto", "Instant",
     "This spell can't be countered.\nCounter target noncreature spell.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Flusterstorm", "Instant",
     "Counter target instant or sorcery spell unless its controller pays {1}.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Blasphemous Act", "Sorcery",
     "This spell costs {1} less to cast for each creature on the battlefield.\n"
     "Blasphemous Act deals 13 damage to each creature.",
     {F.SWEEPER}, {F.SPOT_REMOVAL}),
    ("Toxic Deluge", "Sorcery",
     "As an additional cost to cast this spell, pay X life.\n"
     "All creatures get -X/-X until end of turn.",
     {F.SWEEPER}, set()),
    ("Cyclonic Rift", "Instant",
     "Return target nonland permanent you don't control to its owner's hand.\n"
     "Overload {6}{U}",
     {F.SPOT_REMOVAL}, set()),
    ("Timetwister", "Sorcery",
     "Each player shuffles their hand and graveyard into their library, then draws "
     "seven cards.",
     {F.DRAW}, set()),
    ("Smothering Tithe", "Enchantment",
     "Whenever an opponent draws a card, that player may pay {2}. If the player "
     "doesn't, you create a Treasure token.",
     {F.RAMP}, {F.DRAW}),
    ("Eternal Witness", "Creature — Human Shaman",
     "When this creature enters, return target card from your graveyard to your hand.",
     {F.RECURSION}, {F.SPOT_REMOVAL}),
]


@pytest.mark.parametrize(
    "name,type_line,oracle,must_include,must_exclude",
    REAL_STAPLES,
    ids=[s[0] for s in REAL_STAPLES],
)
def test_real_staples_classify_correctly(name, type_line, oracle, must_include, must_exclude):
    tags = classify(card(name, type_line, oracle))
    assert must_include <= tags, f"{name}: expected {must_include}, got {tags}"
    assert not (must_exclude & tags), f"{name}: must not be {must_exclude & tags}, got {tags}"


def test_classify_deck_keys_by_name():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", produced_mana=frozenset("C"))
    forest = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)",
                  produced_mana=frozenset("G"))
    deck = ResolvedDeck(commanders=(), cards=((1, sol), (36, forest)))
    tags = classify_deck(deck)
    assert tags["Sol Ring"] == frozenset({F.RAMP})
    assert tags["Forest"] == frozenset({F.LAND})
