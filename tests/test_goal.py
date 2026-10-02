import pytest

from mtgpt.goal import Condition, GoalError, condition_names, describe, load_goal

NAMES = ["Test Commander", "Blood Artist", "Viscera Seer", "Craterhoof Behemoth",
         "Spell // Land"]


def load(data, mv=4.0):
    return load_goal(data, deck_names=NAMES, commander_mv=mv)


@pytest.mark.parametrize("archetype, thing_kind, win", [
    ("voltron", "all", Condition("commander_damage", n=63.0)),
    ("go_wide", "count", Condition("opponent_life_lost", n=120.0)),
    ("aristocrats", "all", Condition("opponent_life_lost", n=120.0)),
    ("spellslinger", "all", Condition("opponent_life_lost", n=120.0)),
])
def test_archetype_defaults(archetype, thing_kind, win):
    goal = load({"archetype": archetype})
    assert goal.thing.kind == thing_kind
    assert goal.win == win


def test_big_mana_needs_a_named_win():
    with pytest.raises(GoalError) as err:
        load({"archetype": "big_mana"})
    assert err.value.field == "win"
    goal = load({"archetype": "big_mana", "win": {"cast": "craterhoof behemoth"}})
    assert goal.thing == Condition("mana_available", n=10.0)
    assert goal.win == Condition("cast", names=("Craterhoof Behemoth",))


@pytest.mark.parametrize("archetype", ["combo", "custom"])
def test_combo_and_custom_need_thing_and_win(archetype):
    with pytest.raises(GoalError) as err:
        load({"archetype": archetype, "win": {"opponent_life_lost": 120}})
    assert err.value.field == "thing"


def test_commander_turn_defaults_to_commander_mana_value():
    assert load({"archetype": "go_wide"}, mv=4.0).commander_turn == 4
    assert load({"archetype": "go_wide"}, mv=0.0).commander_turn == 1
    assert load({"archetype": "go_wide", "commander_turn": 3}).commander_turn == 3


def test_names_are_canonicalized_and_mdfcs_match_by_front():
    goal = load({"archetype": "combo",
                 "thing": "commander",
                 "win": {"assembled": ["blood artist", "Spell"]}})
    assert goal.win.names == ("Blood Artist", "Spell // Land")


def test_name_not_in_deck_is_a_hard_stop():
    with pytest.raises(GoalError) as err:
        load({"archetype": "aristocrats", "engine": {"Zulaport Cutthroat": {"sac_outlet": True}}})
    assert err.value.field == "engine"
    assert err.value.values == ("Zulaport Cutthroat",)


def test_engine_spec_parses():
    goal = load({"archetype": "aristocrats", "engine": {
        "Blood Artist": {"on": "creature_dies", "drain": 1},
        "Viscera Seer": {"sac_outlet": True, "priority": "engine"},
    }})
    artist = goal.engine_for("Blood Artist")
    assert (artist.on, artist.drain) == ("creature_dies", 1)
    assert artist.has_tag("drain") and not artist.has_tag("sac_outlet")
    assert goal.engine_for("Viscera Seer").has_tag("sac_outlet")
    assert goal.engine_for("Forest") is None


@pytest.mark.parametrize("data, field", [
    ({"archetype": "elves"}, "archetype"),
    ({"archetype": "go_wide", "colour": "G"}, "(root)"),
    ({"archetype": "go_wide", "win": {"lifegain": 10}}, "win"),
    ({"archetype": "go_wide", "win": {"count": "wizards"}}, "win.count"),
    ({"archetype": "go_wide", "win": {"any": []}}, "win.any"),
    ({"archetype": "go_wide", "win": {"opponent_life_lost": -1}}, "win.opponent_life_lost"),
    ({"archetype": "go_wide", "win": {"cast": "Not A Card"}}, "win.cast"),
    ({"archetype": "go_wide", "commander_turn": 0}, "commander_turn"),
    ({"archetype": "go_wide", "engine": {"Blood Artist": {"on": "lifegain"}}}, "engine.Blood Artist.on"),
    ({"archetype": "go_wide", "engine": {"Blood Artist": {"drain": 1}}}, "engine.Blood Artist.on"),
    ({"archetype": "go_wide", "engine": {"Blood Artist": {"drain": "1", "on": "upkeep"}}}, "engine.Blood Artist.drain"),
    ({"archetype": "go_wide", "engine": {"Blood Artist": {"priority": "first"}}}, "engine.Blood Artist.priority"),
    ({"archetype": "go_wide", "engine": {"Blood Artist": {"flying": True}}}, "engine.Blood Artist"),
    ({"archetype": "go_wide", "disruption": {"board_wipe": 1.5}}, "disruption.board_wipe"),
    ({"archetype": "go_wide", "disruption": {"from_turn": 0}}, "disruption.from_turn"),
])
def test_validation_errors_name_the_field(data, field):
    with pytest.raises(GoalError) as err:
        load(data)
    assert err.value.field == field


def test_disruption_defaults():
    goal = load({"archetype": "go_wide", "disruption": {"commander_removal": 0.15}})
    assert (goal.disruption.commander_removal, goal.disruption.board_wipe,
            goal.disruption.from_turn) == (0.15, 0.0, 4)
    assert load({"archetype": "go_wide"}).disruption is None


def test_condition_names_and_describe():
    goal = load({"archetype": "custom", "thing": "commander", "win": {"any": [
        {"opponent_life_lost": 120}, {"cast": "Craterhoof Behemoth"},
        {"assembled": ["Blood Artist", "Viscera Seer"]}]}})
    assert condition_names(goal.win) == ("Craterhoof Behemoth", "Blood Artist", "Viscera Seer")
    assert [describe(c) for c in goal.win.children] == [
        "opponent_life_lost>=120", "cast:Craterhoof Behemoth",
        "assembled:Blood Artist+Viscera Seer"]


def test_alt_cost_and_override_mana_parse():
    goal = load({"archetype": "go_wide", "engine": {
        "Blood Artist": {"alt_cost": "{W}{U}{B}{R}{G}"},
        "Viscera Seer": {"mana": 3, "mana_colors": "wubrg"}}})
    assert goal.engine_for("Blood Artist").alt_cost == "{W}{U}{B}{R}{G}"
    seer = goal.engine_for("Viscera Seer")
    assert (seer.mana, seer.mana_colors) == (3, frozenset("WUBRG"))


@pytest.mark.parametrize("spec, field", [
    ({"alt_cost": "WUBRG"}, "engine.Blood Artist.alt_cost"),
    ({"alt_cost": "{W}{Q}"}, "engine.Blood Artist.alt_cost"),
    ({"alt_cost": 5}, "engine.Blood Artist.alt_cost"),
    ({"mana": -1, "mana_colors": "G"}, "engine.Blood Artist.mana"),
    ({"mana": 2}, "engine.Blood Artist.mana_colors"),
    ({"mana": 2, "mana_colors": "GX"}, "engine.Blood Artist.mana_colors"),
    ({"mana": 2, "mana_colors": ["G"]}, "engine.Blood Artist.mana_colors"),
])
def test_alt_cost_and_override_mana_validation(spec, field):
    with pytest.raises(GoalError) as err:
        load({"archetype": "go_wide", "engine": {"Blood Artist": spec}})
    assert err.value.field == field


def test_opponent_win_block():
    goal = load({"archetype": "go_wide", "opponent_win": {"from_turn": 5}})
    assert goal.opponent_win.from_turn == 5
    assert goal.opponent_win.answers == ("removal", "counterspell", "stax")
    assert load({"archetype": "go_wide"}).opponent_win is None
    with pytest.raises(GoalError) as err:
        load({"archetype": "go_wide", "opponent_win": {"answers": ["protection"]}})
    assert err.value.field == "opponent_win.answers"
    with pytest.raises(GoalError):
        load({"archetype": "go_wide", "opponent_win": {"from_turn": 0}})


def test_opponent_win_every():
    assert load({"archetype": "go_wide", "opponent_win": {"every": [2, 3]}}).opponent_win.every == (2, 3)
    assert load({"archetype": "go_wide", "opponent_win": {"every": 2}}).opponent_win.every == (2, 2)
    assert load({"archetype": "go_wide", "opponent_win": {}}).opponent_win.every is None
    with pytest.raises(GoalError):
        load({"archetype": "go_wide", "opponent_win": {"every": [3, 2]}})


def test_battlefield_condition_takes_alternatives():
    goal = load({"archetype": "combo", "thing": "commander", "win": {"battlefield": [
        "Blood Artist", ["Viscera Seer", "Craterhoof Behemoth"]]}})
    assert goal.win.kind == "battlefield"
    assert goal.win.slots == (("Blood Artist",), ("Viscera Seer", "Craterhoof Behemoth"))
    assert condition_names(goal.win) == ("Blood Artist", "Viscera Seer", "Craterhoof Behemoth")
    assert describe(goal.win) == "battlefield:Blood Artist+Viscera Seer|Craterhoof Behemoth"


def test_battlefield_condition_names_must_be_in_the_deck():
    with pytest.raises(GoalError) as err:
        load({"archetype": "combo", "thing": "commander",
              "win": {"battlefield": ["Blood Artist", ["Nope"]]}})
    assert err.value.field == "win.battlefield"


def test_removal_engine_parses():
    goal = load({"archetype": "go_wide", "engine": {"Blood Artist": {"removal_engine": True}}})
    assert goal.engine_for("Blood Artist").removal_engine
