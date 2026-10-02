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
    s.lands_played = 1
    assert s.cards[find_card(s, "Odd Spell // Odd Land", s.hand)].unmodeled
    assert choose(s) == {"pass": True}


def test_goal_named_unmodeled_mdfc_is_cast():
    mdfc = card("Odd Spell // Odd Land", "Sorcery // Land",
                "Each player proliferates.", mana_cost="{1}{G}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Odd Spell"}}
    s = rigged(mdfc, hand=["Odd Spell // Odd Land"], lands_in_play=5, goal=goal)
    s.command_zone = []
    s.lands_played = 1
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


# --- Enter the Infinite: cast only when the look-ahead sees the win ----------

ETI = card("Enter the Infinite", "Sorcery", "Draw cards equal to the number of cards in your "
           "library, then put a card from your hand on top of your library. You have no maximum "
           "hand size until your next turn.", mana_cost="{3}")
APPROACH = card("Approach", "Sorcery", "", mana_cost="{2}")
NEXUS = card("Nexus of Fate", "Instant", "Take an extra turn after this one.\nIf Nexus of Fate "
             "would be put into a graveyard from anywhere, reveal Nexus of Fate and shuffle it "
             "into its owner's library instead.", mana_cost="{1}")
CAST_APPROACH = {"archetype": "custom", "thing": "commander", "win": {"cast": "Approach"}}


def test_enter_the_infinite_is_held_when_it_would_not_win():
    s = rigged(ETI, hand=["Enter the Infinite"], lands_in_play=3)  # 3 mana: nothing left after
    s.command_zone = []
    assert choose(s) == {"pass": True}


def test_enter_the_infinite_is_cast_when_the_follow_up_wins():
    s = rigged(ETI, APPROACH, hand=["Enter the Infinite"], lands_in_play=5, goal=CAST_APPROACH)
    s.command_zone = []
    assert choose(s) == {"cast": "Enter the Infinite"}


def test_put_back_prefers_a_self_shuffling_extra_turn():
    s = rigged(ETI, NEXUS, hand=["Enter the Infinite"], lands_in_play=3)
    s.command_zone = []
    s = apply(s, {"cast": "Enter the Infinite"})
    assert choose(s) == {"put_back": "Nexus of Fate"}


# --- The commander comes first from its curve turn on -----------------------------


def test_on_the_curve_turn_the_commander_is_cast_before_ramp():
    s = rigged(SOL_RING, NIGHTS_WHISPER, lands_in_play=4,
               hand=["Sol Ring", "Night's Whisper"])
    s.turn = s.goal.commander_turn  # turn 4 for a 4-drop commander
    assert play_out_turn(s)[0] == "Test Commander"


def test_before_the_curve_turn_ramp_still_comes_first():
    s = rigged(SOL_RING, lands_in_play=5, hand=["Sol Ring"])
    assert s.turn < s.goal.commander_turn
    assert play_out_turn(s)[:2] == ["Sol Ring", "Test Commander"]


def test_a_finisher_that_wins_now_is_cast_before_ramp():
    finisher = card("Finisher", "Sorcery", "", mana_cost="{3}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"}}
    s = rigged(SOL_RING, finisher, hand=["Sol Ring", "Finisher"], lands_in_play=3, goal=goal)
    s.command_zone = []
    assert choose(s) == {"cast": "Finisher"}  # Sol Ring first would leave 2 mana


def test_a_tutor_completes_the_oracle_combo_first():
    oracle = card("Thassa's Oracle", "Creature — Merfolk Wizard", "If X is greater than or equal to the "
                  "number of cards in your library, you win the game.", mana_cost="{U}{U}", power=1.0)
    consult = card("Demonic Consultation", "Instant", "Choose a card name. Reveal cards from the top of "
                   "your library until you reveal a card with the chosen name.", mana_cost="{B}")
    finisher = card("Finisher", "Sorcery", "", mana_cost="{9}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"}}
    s = rigged(DEMONIC_TUTOR, oracle, consult, finisher, hand=["Demonic Tutor", "Thassa's Oracle"],
               lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Demonic Consultation"}


def test_commander_is_held_for_a_same_turn_kill_when_removal_threatens():
    finisher = card("Finisher", "Sorcery", "", mana_cost="{3}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"},
            "disruption": {"commander_removal": 0.2, "from_turn": 1}}
    s = rigged(finisher, hand=["Finisher", "Forest"], lands_in_play=4, goal=goal)
    s.turn = s.goal.commander_turn
    s = apply(s, choose(s))  # land: 5 mana, commander 4 + finisher 3 needs 7
    assert choose(s) != {"cast": "Test Commander"}


def test_commander_is_not_held_without_removal_risk():
    finisher = card("Finisher", "Sorcery", "", mana_cost="{3}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"}}
    s = rigged(finisher, hand=["Finisher", "Forest"], lands_in_play=4, goal=goal)
    s.turn = s.goal.commander_turn
    s = apply(s, choose(s))
    from mtgpt.goldfish.policy import _hold_commander_for_kill
    assert not _hold_commander_for_kill(s, s.command_zone[0])


def test_a_tutor_fetches_an_answer_when_an_attempt_is_coming_and_none_is_held():
    finisher = card("Finisher", "Sorcery", "", mana_cost="{9}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"},
            "opponent_win": {"from_turn": 2}}
    s = rigged(DEMONIC_TUTOR, SWORDS, finisher, hand=["Demonic Tutor"], lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Swords to Plowshares"}


def test_with_an_answer_held_the_tutor_goes_for_the_finisher():
    finisher = card("Finisher", "Sorcery", "", mana_cost="{9}")
    counter = card("Counterspell", "Instant", "Counter target spell.", mana_cost="{U}{U}")
    goal = {"archetype": "custom", "thing": "commander", "win": {"cast": "Finisher"},
            "opponent_win": {"from_turn": 2}}
    s = rigged(DEMONIC_TUTOR, SWORDS, finisher, counter, hand=["Demonic Tutor", "Counterspell"],
               lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Finisher"}


# --- Battlefield combos: closest combo first ---------------------------------

PIECE_C = card("Piece C", "Artifact", "", mana_cost="{2}")
PIECE_D = card("Piece D", "Artifact", "", mana_cost="{2}")


def test_battlefield_win_needs_the_pieces_on_the_battlefield_not_in_hand():
    from mtgpt.goldfish.engine import evaluate
    goal = {"archetype": "combo", "thing": "commander",
            "win": {"battlefield": ["Piece A", ["Piece B", "Piece C"]]}}
    in_hand = rigged(PIECE_A, PIECE_B, PIECE_C, hand=["Piece A", "Piece B"], goal=goal)
    assert not evaluate(in_hand, in_hand.goal.win)
    out = rigged(PIECE_A, PIECE_B, PIECE_C, on_board=["Piece A", "Piece C"], goal=goal)
    assert evaluate(out, out.goal.win)


def test_tutor_completes_the_combo_closest_to_done():
    # Combo 1 (A+B) is listed first but is missing both pieces; combo 2 (C+D)
    # has C out already, so the tutor finds D.
    goal = {"archetype": "combo", "thing": "commander", "win": {"any": [
        {"battlefield": ["Piece A", "Piece B"]}, {"battlefield": ["Piece C", "Piece D"]}]}}
    s = rigged(DEMONIC_TUTOR, PIECE_A, PIECE_B, PIECE_C, PIECE_D, hand=["Demonic Tutor"],
               on_board=["Piece C"], lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Piece D"}


def test_tutor_counts_a_piece_in_hand_as_found():
    goal = {"archetype": "combo", "thing": "commander", "win": {"any": [
        {"battlefield": ["Piece C", "Piece D", "Piece A"]}, {"battlefield": ["Piece A", "Piece B"]}]}}
    s = rigged(DEMONIC_TUTOR, PIECE_A, PIECE_B, PIECE_C, PIECE_D, hand=["Demonic Tutor", "Piece A"],
               lands_in_play=2, goal=goal)
    s.command_zone = []
    s = apply(s, {"cast": "Demonic Tutor"})
    assert choose(s) == {"tutor": "Piece B"}
