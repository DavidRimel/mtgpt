import json

import pytest

from mtgpt import card_rules
from mtgpt.goal import GoalError
from mtgpt.goldfish.engine import prepare

from simdeck import BEAR, card, deck

ARTIST = card("Blood Artist", "Creature — Vampire", "Whenever Blood Artist or another creature "
              "dies, target player loses 1 life and you gain 1 life.", mana_cost="{1}{B}", power=0.0)


@pytest.fixture
def library(empty_card_rules):
    return empty_card_rules


def test_record_validates_and_saves_a_rule(library):
    card_rules.record("Blood Artist", status="override",
                      rule={"on": "creature_dies", "drain": 1}, note="drain on any death")
    saved = json.loads(library.read_text())["cards"]["Blood Artist"]
    assert saved["status"] == "override" and saved["rule"] == {"on": "creature_dies", "drain": 1}
    assert saved["note"] == "drain on any death" and saved["reviewed"]


def test_record_rejects_a_bad_rule(library):
    with pytest.raises(GoalError):
        card_rules.record("Blood Artist", status="override", rule={"on": "lifegain"}, note="x")
    with pytest.raises(ValueError):
        card_rules.record("Blood Artist", status="maybe", rule=None, note="x")
    with pytest.raises(ValueError):
        card_rules.record("Blood Artist", status="override", rule=None, note="x")


def test_library_rules_apply_when_the_goal_has_none(library):
    card_rules.record("Blood Artist", status="override",
                      rule={"on": "creature_dies", "drain": 1}, note="")
    setup = prepare(deck(ARTIST, BEAR), {"archetype": "go_wide"})
    assert setup.goal.engine_for("Blood Artist").drain == 1
    assert "Blood Artist" in setup.goal_raw["engine"]  # survives pilot-mode serialization


def test_the_goal_file_beats_the_library(library):
    card_rules.record("Blood Artist", status="override",
                      rule={"on": "creature_dies", "drain": 1}, note="")
    goal = {"archetype": "go_wide", "engine": {"Blood Artist": {"on": "creature_dies", "drain": 2}}}
    assert prepare(deck(ARTIST, BEAR), goal).goal.engine_for("Blood Artist").drain == 2


def test_parsed_and_ignored_entries_add_no_rule(library):
    card_rules.record("Grizzly Bears", status="parsed", rule=None, note="vanilla")
    card_rules.record("Blood Artist", status="ignored", rule=None, note="opponents only")
    setup = prepare(deck(ARTIST, BEAR), {"archetype": "go_wide"})
    assert setup.goal.engine_for("Blood Artist") is None


def test_scan_reports_model_library_status_and_what_needs_review(library):
    card_rules.record("Grizzly Bears", status="parsed", rule=None, note="vanilla")
    report = card_rules.scan(deck(ARTIST, BEAR))
    rows = {r["name"]: r for r in report["cards"]}
    assert rows["Grizzly Bears"]["library"] == "parsed"
    assert rows["Blood Artist"]["library"] is None
    assert rows["Blood Artist"]["unmodeled"] is True
    assert "Blood Artist" in report["needs_review"]
    assert "Grizzly Bears" not in report["needs_review"]
    assert "Forest" not in report["needs_review"]  # a basic needs no review


def test_shipped_library_is_valid():
    data = card_rules.load(card_rules.SHIPPED_PATH)
    for name, entry in data["cards"].items():
        assert entry["status"] in card_rules.STATUSES, name
        if entry["status"] == "override":
            card_rules.validate_rule(name, entry["rule"])
