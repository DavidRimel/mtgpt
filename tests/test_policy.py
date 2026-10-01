from mtgpt.goldfish.engine import apply, find_card
from mtgpt.goldfish.policy import choose

from simdeck import DEMONIC_TUTOR, NEVER, NIGHTS_WHISPER, SOL_RING, SWORDS, card, rigged

HOOF = card("Craterhoof Behemoth", "Creature — Beast", "", mana_cost="{5}{G}{G}{G}", power=5.0)
PIECE_A = card("Piece A", "Artifact", "", mana_cost="{2}")
PIECE_B = card("Piece B", "Artifact", "", mana_cost="{2}")
GUILDGATE = card("Gate", "Land — Gate", "This land enters tapped.\n{T}: Add {G}.",
                 produced_mana="G")


def play_out_turn(s):
    """Apply the policy until it passes; return the names it cast, in order."""
    cast = []
    while True:
        action = choose(s)
        if "pass" in action:
            return cast
        if "cast" in action:
            cast.append(action["cast"])
        s = apply(s, action, in_place=True)


def test_priority_is_ramp_then_commander_then_value_and_never_interaction():
    s = rigged(SOL_RING, NIGHTS_WHISPER, SWORDS, lands_in_play=8,
               hand=["Night's Whisper", "Swords to Plowshares", "Sol Ring"])
    assert play_out_turn(s) == ["Sol Ring", "Test Commander", "Night's Whisper"]


def test_interaction_alone_is_held():
    s = rigged(SWORDS, hand=["Swords to Plowshares"], lands_in_play=3)
    s.command_zone = []
    assert choose(s) == {"pass": True}


def test_tapped_land_is_played_when_the_untapped_one_casts_nothing_more():
    s = rigged(GUILDGATE, hand=["Gate", "Forest"])
    assert choose(s) == {"play_land": "Gate"}


def test_untapped_land_is_played_when_it_casts_more():
    bear = card("Grizzly Bears", "Creature — Bear", "", mana_cost="{1}{G}", power=2.0)
    s = rigged(GUILDGATE, bear, hand=["Gate", "Forest", "Grizzly Bears"], lands_in_play=1)
    assert choose(s) == {"play_land": "Forest"}


def test_tapped_land_on_a_turn_the_untapped_one_only_floats_mana():
    # Three lands out and a 3-drop in hand: the untapped fourth land adds mana
    # nothing can spend, so the tapped land goes down now.
    bear = card("Grizzly Bears", "Creature — Bear", "", mana_cost="{2}{G}", power=2.0)
    s = rigged(GUILDGATE, bear, hand=["Gate", "Forest", "Grizzly Bears"], lands_in_play=3)
    s.command_zone = []
    assert choose(s) == {"play_land": "Gate"}


def test_land_choice_never_casts_less_than_untapped_first():
    # Only the untapped land lets Bears resolve this turn, so it must be chosen
    # and Bears must follow.
    bear = card("Grizzly Bears", "Creature — Bear", "", mana_cost="{1}{G}", power=2.0)
    s = rigged(GUILDGATE, bear, hand=["Gate", "Forest", "Grizzly Bears"], lands_in_play=1)
    s.command_zone = []
    apply(s, choose(s), in_place=True)
    assert play_out_turn(s) == ["Grizzly Bears"]


def test_tutor_finds_the_missing_combo_piece():
    goal = {"archetype": "combo", "thing": {"assembled": ["Piece A", "Piece B"]},
            "win": {"assembled": ["Piece A", "Piece B"]}}
    s = rigged(DEMONIC_TUTOR, PIECE_A, PIECE_B, hand=["Demonic Tutor", "Piece A"],
               lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Piece B"}


def test_hold_card_is_cast_only_when_it_wins():
    goal = {"archetype": "custom", "thing": "commander",
            "win": {"cast": "Craterhoof Behemoth"},
            "engine": {"Craterhoof Behemoth": {"priority": "hold"}}}
    wins = rigged(HOOF, hand=["Craterhoof Behemoth"], lands_in_play=8, goal=goal)
    wins.command_zone = []
    assert choose(wins) == {"cast": "Craterhoof Behemoth"}

    goal["win"] = NEVER
    holds = rigged(HOOF, hand=["Craterhoof Behemoth"], lands_in_play=8, goal=goal)
    holds.command_zone = []
    assert choose(holds) == {"pass": True}


# --- Interaction permanents, goal-named interaction, unmodeled MDFCs ---------

SWORD = card("Sword of Fire and Ice", "Artifact — Equipment",
             "Equipped creature gets +2/+2 and has protection from red and from blue.\n"
             "Equip {2}", mana_cost="{3}")
GREAVES = card("Lightning Greaves", "Artifact — Equipment",
               "Equipped creature has shroud and haste.\nEquip {0}", mana_cost="{2}")
PLATE = card("Darksteel Plate", "Artifact — Equipment",
             "Indestructible\nEquipped creature has indestructible.\nEquip {2}", mana_cost="{3}")
CHUPACABRA = card("Ravenous Chupacabra", "Creature — Horror",
                  "When this creature enters, destroy target creature an opponent controls.",
                  mana_cost="{2}{G}{G}", power=2.0)
VOLTRON = {"archetype": "voltron"}


def test_voltron_casts_its_protective_equipment():
    s = rigged(SWORD, GREAVES, PLATE, lands_in_play=12, commander_out=True,
               hand=["Sword of Fire and Ice", "Lightning Greaves", "Darksteel Plate"],
               goal=VOLTRON)
    assert sorted(play_out_turn(s)) == ["Darksteel Plate", "Lightning Greaves",
                                       "Sword of Fire and Ice"]


def test_voltron_equipment_deck_comes_online():
    from mtgpt.goldfish.run import simulate
    from simdeck import deck
    report = simulate(deck(*[SWORD] * 3, *[GREAVES] * 3, *[PLATE] * 2, lands=37),
                      VOLTRON, games=100)
    assert report["thing"]["online_rate"] > 0.9


def test_interaction_permanent_outside_voltron_is_cast_after_value():
    s = rigged(CHUPACABRA, NIGHTS_WHISPER, lands_in_play=12,
               hand=["Ravenous Chupacabra", "Night's Whisper"])
    s.command_zone = []
    assert play_out_turn(s)[:2] == ["Night's Whisper", "Ravenous Chupacabra"]


def test_goal_named_interaction_can_win():
    from mtgpt.goldfish.run import simulate
    from simdeck import deck
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Swords to Plowshares"}}
    report = simulate(deck(SWORDS, lands=60), goal, games=50)
    assert report["win"]["win_rate"] > 0


def test_unmodeled_mdfc_spell_face_is_kept_as_a_land():
    mdfc = card("Odd Spell // Odd Land", "Sorcery // Land",
                "Each player proliferates.", mana_cost="{1}{G}")
    s = rigged(mdfc, hand=["Odd Spell // Odd Land"], lands_in_play=5)
    s.command_zone = []
    s.land_played = True
    assert s.cards[find_card(s, "Odd Spell // Odd Land", s.hand)].unmodeled
    assert choose(s) == {"pass": True}


def test_goal_named_unmodeled_mdfc_is_cast():
    mdfc = card("Odd Spell // Odd Land", "Sorcery // Land",
                "Each player proliferates.", mana_cost="{1}{G}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Odd Spell"}}
    s = rigged(mdfc, hand=["Odd Spell // Odd Land"], lands_in_play=5, goal=goal)
    s.command_zone = []
    s.land_played = True
    assert choose(s) == {"cast": "Odd Spell // Odd Land"}


def test_land_shortcut_counts_an_alternative_cost():
    # Jodah out with 4 lands: a 10-drop costs WUBRG, so the untapped fifth land
    # casts it and must be played over the tapped one.
    from mtgpt.models import ResolvedDeck
    prism = card("Prism Land", "Land", "{T}: Add one mana of any color.", produced_mana="WUBRG",
                 identity="WUBRG", colors="")
    prism_gate = card("Prism Gate", "Land", "This land enters tapped.\n{T}: Add one mana of any color.",
                      produced_mana="WUBRG", identity="WUBRG", colors="")
    big = card("Big Spell", "Sorcery", "", mana_cost="{7}{U}{U}{U}", identity="U")
    jodah = card("Jodah", "Legendary Creature — Human Wizard", "", mana_cost="{1}{U}{R}{W}",
                 power=3.0, identity="WUBRG")
    d = ResolvedDeck(commanders=(jodah,), cards=((1, big), (1, prism_gate), (97, prism)))
    goal = {"archetype": "custom", "thing": "commander", "win": NEVER,
            "engine": {"Jodah": {"alt_cost": "{W}{U}{B}{R}{G}"}}}
    s = rigged(source=d, land="Prism Land", lands_in_play=4, commander_out=True,
               hand=["Big Spell", "Prism Gate", "Prism Land"], goal=goal)
    assert choose(s) == {"play_land": "Prism Land"}
