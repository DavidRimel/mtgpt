from mtgpt.goldfish.engine import apply
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


def test_untapped_land_is_played_before_a_tapped_one():
    s = rigged(GUILDGATE, hand=["Gate", "Forest"])
    assert choose(s) == {"play_land": "Forest"}


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
