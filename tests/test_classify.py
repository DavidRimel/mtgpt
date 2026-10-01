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


@pytest.mark.parametrize(
    "name,type_line,oracle,expected",
    [
        # Pump spells are not removal.
        ("Giant Growth", "Instant", "Target creature gets +3/+3 until end of turn.", {F.SYNERGY}),
        ("Mutagenic Growth", "Instant", "Target creature gets +2/+2 until end of turn.", {F.SYNERGY}),
        # Blink of your own creature is protection, not removal.
        ("Ephemerate", "Instant",
         "Exile target creature you control, then return that card to the battlefield "
         "under its owner's control.", {F.PROTECTION}),
        ("Restoration Angel", "Creature — Angel",
         "Flash\nFlying\nWhen this creature enters, you may exile target non-Angel creature "
         "you control, then return that card to the battlefield under its owner's control.",
         {F.PROTECTION}),
        # Adjective-modified and sacrifice-form land denial are not creature wipes.
        ("Ruination", "Sorcery", "Destroy all nonbasic lands.", {F.MASS_LAND_DENIAL}),
        ("Bust", "Sorcery", "Each player sacrifices all lands they control except for one.",
         {F.MASS_LAND_DENIAL}),
        ("Armageddon", "Sorcery", "Destroy all lands.", {F.MASS_LAND_DENIAL}),
        # A multi-type sacrifice effect is a sweeper even though it also takes a
        # land. An earlier draft's negative lookahead wrongly suppressed this.
        ("Release", "Sorcery",
         "Each player sacrifices an artifact, a creature, an enchantment, a land, "
         "and a planeswalker of their choice.",
         {F.SWEEPER}),
        # ...but a wipe that names non-land types is genuinely both.
        ("Jokulhaups", "Sorcery",
         "Destroy all artifacts, creatures, and lands. They can't be regenerated.",
         {F.MASS_LAND_DENIAL, F.SWEEPER}),
        ("Devastation", "Sorcery", "Destroy all creatures and lands.",
         {F.MASS_LAND_DENIAL, F.SWEEPER}),
        # A counterspell's self-referential clause is not protection.
        ("Dovin's Veto", "Instant",
         "This spell can't be countered.\nCounter target noncreature spell.",
         {F.COUNTERSPELL}),
        # "you lose the game" is a drawback, not a wincon.
        ("Pact of Negation", "Instant",
         "Counter target spell. At the beginning of your next upkeep, pay {3}{U}{U}. "
         "If you don't, you lose the game.", {F.COUNTERSPELL}),
        # Library exile is not a board wipe.
        ("Demonic Consultation", "Instant",
         "Name a card. Exile the top six cards of your library, then reveal cards from the "
         "top of your library until you reveal the named card. Put that card into your hand "
         "and exile all other cards revealed this way.", {F.SYNERGY}),
        # Indirect quantification still reads as a land fetch.
        ("Scapeshift", "Sorcery",
         "Sacrifice any number of lands. Search your library for that many land cards, put "
         "them onto the battlefield tapped, then shuffle.", {F.RAMP}),
        # Damage is removal; damage to each creature is a sweeper.
        ("Lightning Bolt", "Instant", "Lightning Bolt deals 3 damage to any target.",
         {F.SPOT_REMOVAL}),
        ("Flame Slash", "Sorcery", "Flame Slash deals 4 damage to target creature.",
         {F.SPOT_REMOVAL}),
        ("Pyroclasm", "Sorcery", "Pyroclasm deals 2 damage to each creature.", {F.SWEEPER}),
    ],
    ids=lambda v: v if isinstance(v, str) and " " not in v else None,
)
def test_false_positive_regressions(name, type_line, oracle, expected):
    """Each case here was wrongly classified by an earlier draft. Keep them."""
    assert classify(card(name, type_line, oracle)) == expected


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


# --- Protection means protection GRANTED (classify win a) --------------------


@pytest.mark.parametrize(
    "name,type_line,oracle,keywords",
    [
        # Scryfall populates `keywords` for SELF-granted keywords, and the oracle
        # text states them the same way. These three are fatties that protect
        # only themselves; the PROTECTION band is 3-5, so three of them filled it
        # and the user was told to add no protection — then lost the commander to
        # the next Swords to Plowshares.
        ("Blightsteel Colossus", "Artifact Creature — Phyrexian Golem",
         "Infect, trample\nBlightsteel Colossus is indestructible.\n"
         "When Blightsteel Colossus is put into a graveyard from anywhere, reveal it "
         "and shuffle it into its owner's library.",
         ("Infect", "Trample", "Indestructible")),
        ("Carnage Tyrant", "Creature — Dinosaur",
         "This spell can't be countered.\nTrample, hexproof",
         ("Trample", "Hexproof")),
        ("Toski, Bearer of Secrets", "Legendary Creature — Squirrel",
         "Toski, Bearer of Secrets is indestructible.\n"
         "Whenever a creature you control deals combat damage to a player, draw a card.",
         ("Indestructible",)),
    ],
    ids=["blightsteel", "carnage-tyrant", "toski"],
)
def test_a_self_only_keyword_is_not_the_decks_protection(name, type_line, oracle, keywords):
    tags = classify(card(name, type_line, oracle, keywords=keywords))
    assert F.PROTECTION not in tags, f"{name}: {sorted(t.value for t in tags)}"


@pytest.mark.parametrize(
    "name,type_line,oracle",
    [
        ("Heroic Intervention", "Instant",
         "Permanents you control gain hexproof and indestructible until end of turn."),
        ("Teferi's Protection", "Instant",
         "Until your next turn, your life total can't change, you gain protection from "
         "everything, and all permanents you control phase out."),
        ("Mother of Runes", "Creature — Human Cleric",
         "{T}: Target creature you control gains protection from the color of your "
         "choice until end of turn."),
        ("Swiftfoot Boots", "Artifact — Equipment",
         "Equipped creature has hexproof and haste.\nEquip {1}"),
        ("Tamiyo's Safekeeping", "Instant",
         "Target permanent you control gains hexproof and indestructible until end of "
         "turn. You gain 2 life."),
    ],
    ids=["heroic-intervention", "teferis-protection", "mother-of-runes",
         "swiftfoot-boots", "tamiyos-safekeeping"],
)
def test_granted_protection_is_still_protection(name, type_line, oracle):
    """The narrowing must not cost the real protection package."""
    assert F.PROTECTION in classify(card(name, type_line, oracle))


# --- Overload makes a spot-removal spell a sweeper (classify win c) ----------


CYCLONIC_RIFT_TEXT = (
    "Return target nonland permanent you don't control to its owner's hand.\n"
    "Overload {6}{U}{U} (You may cast this spell for its overload cost. If you do, "
    'change "target" in its text to "each.")'
)
VANDALBLAST_TEXT = (
    "Destroy target artifact you don't control.\n"
    "Overload {4}{R} (You may cast this spell for its overload cost. If you do, "
    'change "target" in its text to "each.")'
)


@pytest.mark.parametrize(
    "name,type_line,oracle",
    [("Cyclonic Rift", "Instant", CYCLONIC_RIFT_TEXT),
     ("Vandalblast", "Sorcery", VANDALBLAST_TEXT)],
    ids=["cyclonic-rift", "vandalblast"],
)
def test_overload_spells_are_sweepers(name, type_line, oracle):
    """The format's two most-played pseudo-wraths tagged `spot_removal` only. The
    Sweeper band is 2-3, so one miss is a third of the band. Their oracle text
    states the substitution verbatim, so applying it is reading the card.
    """
    tags = classify(card(name, type_line, oracle, keywords=("Overload",)))
    assert F.SWEEPER in tags, sorted(t.value for t in tags)
    # Both modes are real, so the targeted one is not displaced.
    assert F.SPOT_REMOVAL in tags, sorted(t.value for t in tags)


def test_the_same_text_without_the_overload_keyword_is_not_a_sweeper():
    """The substitution is keyed off the keyword, not off the word "target"
    appearing anywhere — otherwise every targeted removal spell becomes a wipe.
    """
    tags = classify(card("Cyclonic Rift", "Instant", CYCLONIC_RIFT_TEXT, keywords=()))
    assert F.SWEEPER not in tags
    assert F.SPOT_REMOVAL in tags


@pytest.mark.parametrize(
    "name,oracle",
    [("Swords to Plowshares",
      "Exile target creature. Its controller gains life equal to its power."),
     ("Beast Within",
      "Destroy target permanent. Its controller creates a 3/3 green Beast creature token."),
     ("Lightning Bolt", "Lightning Bolt deals 3 damage to any target.")],
    ids=["swords", "beast-within", "bolt"],
)
def test_non_overload_removal_is_unaffected(name, oracle):
    tags = classify(card(name, "Instant", oracle))
    assert F.SWEEPER not in tags
    assert F.SPOT_REMOVAL in tags


def test_an_overload_spell_that_is_not_a_wipe_stays_spot_removal():
    """Overload does not make every spell a sweeper. Mizzix's Mastery overloads
    into multiple copies, not a board wipe."""
    tags = classify(card(
        "Mizzix's Mastery", "Sorcery",
        "Exile target card from your graveyard. Copy it, and you may cast the copy "
        "without paying its mana cost.\nOverload {5}{R}{R}{R}",
        keywords=("Overload",),
    ))
    assert F.SWEEPER not in tags


def test_mass_bounce_is_a_sweeper_in_its_own_right():
    """The branch the overload substitution relies on is correct unconditionally:
    Evacuation is a pseudo-wrath without any keyword."""
    assert F.SWEEPER in classify(
        card("Evacuation", "Instant", "Return all creatures to their owners' hands.")
    )


@pytest.mark.parametrize(
    "name,type_line,oracle",
    [
        # Each of these names itself on the very line that grants a keyword to
        # OTHER permanents. A first draft of the self-reference mask blanked any
        # oracle line containing the card's name and lost all four; measured
        # against 400 real Commander-legal cards that grant hexproof or
        # indestructible, they were the whole regression. The mask therefore drops
        # only the clause where the card is the SUBJECT.
        ("Archangel Avacyn", "Legendary Creature — Angel",
         "Flash\nFlying, vigilance\nWhen Archangel Avacyn enters, creatures you "
         "control gain indestructible until end of turn."),
        ("Zack Fair", "Legendary Creature — Hero Soldier",
         "Zack Fair enters with a +1/+1 counter on it.\n{1}, Sacrifice Zack Fair: "
         "Target creature you control gains indestructible until end of turn."),
        ("Thancred Waters", "Legendary Creature — Human Rogue",
         "Flash\nRoyal Guard — When Thancred Waters enters, another target legendary "
         "permanent you control gains indestructible until end of turn."),
        ("Stonehoof Chieftain", "Creature — Beast",
         "Trample, indestructible\nWhenever another creature you control attacks, it "
         "gains trample and indestructible until end of turn."),
    ],
    ids=["archangel-avacyn", "zack-fair", "thancred-waters", "stonehoof-chieftain"],
)
def test_a_card_that_names_itself_while_granting_to_others_is_protection(
    name, type_line, oracle
):
    assert F.PROTECTION in classify(card(name, type_line, oracle))


@pytest.mark.parametrize(
    "name,type_line,oracle,keywords",
    [
        ("Tromokratis", "Legendary Creature — Kraken",
         "Tromokratis has hexproof unless it's attacking or blocking.",
         ("Hexproof",)),
        ("Dragonlord Ojutai", "Legendary Creature — Elder Dragon",
         "Flying\nDragonlord Ojutai has hexproof as long as it's untapped.",
         ("Flying", "Hexproof")),
        ("Myojin of Life's Web", "Legendary Creature — Spirit",
         "Myojin of Life's Web enters with a divinity counter on it if you cast it "
         "from your hand.\nMyojin of Life's Web has indestructible as long as it has "
         "a divinity counter on it.",
         ("Indestructible",)),
    ],
    ids=["tromokratis", "dragonlord-ojutai", "myojin"],
)
def test_a_conditional_self_only_keyword_is_still_not_protection(
    name, type_line, oracle, keywords
):
    """The mask has to survive the clause continuing past the keyword."""
    tags = classify(card(name, type_line, oracle, keywords=keywords))
    assert F.PROTECTION not in tags, sorted(t.value for t in tags)
