# tests/test_cli.py
import io
import json
import pathlib

from mtgpt import cli

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_every_subcommand_is_registered():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subcommand dest named 'command'"
    assert set(actions[0].choices) == {
        "card", "search", "classify", "read", "validate", "audit", "bracket", "report",
        "synergy", "themes", "combos", "card-combos", "suggest",
        "goldfish", "goldfish-compare", "goldfish-new", "goldfish-step",
    }


def test_card_emits_a_success_envelope(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "lookup_card",
                        lambda name, client=None: {"name": name, "functions": ["ramp"]})
    assert cli.main(["card", "Sol Ring"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "card"
    assert payload["data"]["name"] == "Sol Ring"


def test_unresolved_card_emits_a_machine_readable_error(monkeypatch, capsys):
    from mtgpt.errors import UnresolvedCards

    def boom(name, client=None):
        raise UnresolvedCards(["Blatantly Fake Card"])

    monkeypatch.setattr(cli.api, "lookup_card", boom)
    code = cli.main(["card", "Blatantly Fake Card"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnresolvedCards"
    # The offending names are data, so the agent can act on them.
    assert payload["error"]["names"] == ["Blatantly Fake Card"]


def test_search_passes_limit_through(monkeypatch):
    seen = {}

    def fake(query, limit=25, client=None):
        seen["query"], seen["limit"] = query, limit
        return {"query": query, "count": 0, "cards": []}

    monkeypatch.setattr(cli.api, "search_cards", fake)
    assert cli.main(["search", "c:g t:sorcery", "--limit", "5"]) == 0
    assert seen == {"query": "c:g t:sorcery", "limit": 5}


def test_classify_accepts_several_names(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "classify_cards",
                        lambda names, client=None: {n: ["ramp"] for n in names})
    assert cli.main(["classify", "Sol Ring", "Arcane Signet"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["data"]) == {"Sol Ring", "Arcane Signet"}


def test_read_needs_no_network(capsys):
    assert cli.main(["read", "--file", str(FIXTURES / "sample_deck.txt")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total_cards"] == 41


def test_deck_subcommands_accept_stdin(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "audit_deck", lambda text, client=None: {"land_count": 36})
    monkeypatch.setattr("sys.stdin", io.StringIO("1 Sol Ring\n36 Forest\n"))
    assert cli.main(["audit", "--stdin"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["land_count"] == 36


def test_bracket_passes_target_through(monkeypatch):
    seen = {}

    def fake(text, target=3, client=None):
        seen["target"] = target
        return {"target": target, "target_name": "Core"}

    monkeypatch.setattr(cli.api, "bracket_check", fake)
    cli.main(["bracket", "--file", str(FIXTURES / "sample_deck.txt"), "--target", "2"])
    assert seen["target"] == 2


def test_report_text_mode_renders_instead_of_json(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "full_report",
                        lambda text, target=3, client=None, combos=False: {"stub": True})
    monkeypatch.setattr(cli.api, "render_report", lambda report: "RENDERED REPORT")
    assert cli.main(["report", "--file", str(FIXTURES / "sample_deck.txt"), "--text"]) == 0
    out = capsys.readouterr().out
    assert "RENDERED REPORT" in out
    assert "{" not in out


def test_missing_deck_input_is_a_user_error(capsys):
    assert cli.main(["audit"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Export" in payload["error"]["message"]


def test_unparseable_deck_is_reported_as_data(capsys, tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("not a decklist\n")
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "DeckStructureError"


# --- Non-UTF-8 decklists stay inside the JSON envelope (Important 4) ---------


def test_a_cp1252_decklist_is_a_user_error_not_a_traceback(capsys, tmp_path):
    """A Windows editor's "ANSI" encoding turns Urza's curly apostrophe into a
    byte that is not valid UTF-8. `UnicodeDecodeError` is a `ValueError`, so the
    old `except OSError` let it escape as a raw traceback with exit 1 — outside
    the envelope every other failure respects. Card names are full of apostrophes.
    """
    path = tmp_path / "deck.txt"
    path.write_bytes("Deck\n1 Urza’s Tower\n".encode("cp1252"))
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnicodeDecodeError"
    # The message has to tell the user what to do about it.
    assert "UTF-8" in payload["error"]["message"]


def test_a_utf16_decklist_is_a_user_error_not_a_traceback(capsys, tmp_path):
    """UTF-16 is what Windows PowerShell 5 produces from `... > deck.txt`."""
    path = tmp_path / "deck.txt"
    path.write_bytes("Deck\n1 Sol Ring\n".encode("utf-16"))
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnicodeDecodeError"


def test_a_utf8_bom_decklist_still_parses(capsys, tmp_path):
    """A UTF-8 BOM is valid UTF-8, so it must not be caught by the above:
    deckparse already strips it."""
    path = tmp_path / "deck.txt"
    path.write_bytes("﻿Deck\n1 Sol Ring\n36 Forest\n".encode("utf-8"))
    assert cli.main(["read", "--file", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total_cards"] == 37


# --- Goldfish ----------------------------------------------------------------


def goal_file(tmp_path, data=None):
    path = tmp_path / "deck.goal.json"
    path.write_text(json.dumps(data or {"archetype": "go_wide"}))
    return str(path)


def test_goldfish_passes_options_through(monkeypatch, capsys, tmp_path):
    seen = {}

    def fake(text, goal, **options):
        seen.update(goal=goal, **options)
        return {"games": options["games"]}

    monkeypatch.setattr(cli.api, "goldfish", fake)
    code = cli.main(["goldfish", "--file", str(FIXTURES / "sample_deck.txt"),
                     "--goal", goal_file(tmp_path), "--games", "50", "--turns", "8",
                     "--seed", "3", "--no-disruption"])
    assert code == 0
    assert seen == {"goal": {"archetype": "go_wide"}, "games": 50, "turns": 8,
                    "seed": 3, "disruption": False, "client": None}


def test_goldfish_rejects_a_malformed_goal_file(capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    code = cli.main(["goldfish", "--file", str(FIXTURES / "sample_deck.txt"), "--goal", str(bad)])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["error"]["type"] == "JSONDecodeError"


def test_goldfish_compare_needs_two_files(capsys, tmp_path):
    deck = str(FIXTURES / "sample_deck.txt")
    code = cli.main(["goldfish-compare", "--file", deck, "--goal", goal_file(tmp_path)])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "MissingInput"


def test_pilot_new_writes_state_and_step_rewrites_it(monkeypatch, capsys, tmp_path):
    out = tmp_path / "game.json"
    monkeypatch.setattr(cli.api, "goldfish_new",
                        lambda text, goal, **o: {"state": {"turn": 1}, "view": {"turn": 1}})
    assert cli.main(["goldfish-new", "--file", str(FIXTURES / "sample_deck.txt"),
                     "--goal", goal_file(tmp_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text()) == {"turn": 1}
    assert json.loads(capsys.readouterr().out)["data"] == {"turn": 1}

    def step(state, action):
        assert (state, action) == ({"turn": 1}, {"pass": True})
        return {"state": {"turn": 2}, "view": {"turn": 2}}

    monkeypatch.setattr(cli.api, "goldfish_step", step)
    assert cli.main(["goldfish-step", "--state", str(out), "--action", '{"pass": true}']) == 0
    assert json.loads(out.read_text()) == {"turn": 2}


def test_pilot_illegal_action_is_a_user_error(monkeypatch, capsys, tmp_path):
    from mtgpt.goldfish.engine import IllegalAction

    state = tmp_path / "game.json"
    state.write_text("{}")

    def step(state, action):
        raise IllegalAction(action, [{"pass": True}])

    monkeypatch.setattr(cli.api, "goldfish_step", step)
    code = cli.main(["goldfish-step", "--state", str(state), "--action", '{"cast": "X"}'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["error"]["legal_actions"] == [{"pass": True}]
    assert json.loads(state.read_text()) == {}, "a refused action must not touch the game"


def _envelope_error(capsys):
    return json.loads(capsys.readouterr().out)["error"]


def test_goldfish_goal_file_must_be_an_object(capsys, tmp_path):
    code = cli.main(["goldfish", "--file", str(FIXTURES / "sample_deck.txt"),
                     "--goal", _raw(tmp_path, "null")])
    assert code == 2
    assert _envelope_error(capsys)["type"] == "JSONDecodeError"


def _raw(tmp_path, text, name="raw.json"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_goldfish_step_state_file_must_be_an_object(capsys, tmp_path):
    state = _raw(tmp_path, "null", "state.json")
    assert cli.main(["goldfish-step", "--state", state, "--action", "{}"]) == 2
    assert _envelope_error(capsys)["type"] == "JSONDecodeError"


def test_goldfish_step_action_must_be_an_object(capsys, tmp_path):
    state = _raw(tmp_path, "{}", "state.json")
    for bad in ("null", "[1]"):
        assert cli.main(["goldfish-step", "--state", state, "--action", bad]) == 2
        err = _envelope_error(capsys)
        assert (err["type"], err["field"]) == ("JSONDecodeError", "--action")


def test_goldfish_step_malformed_state_leaves_file_untouched(capsys, tmp_path):
    state = _raw(tmp_path, "{not json", "state.json")
    assert cli.main(["goldfish-step", "--state", state, "--action", "{}"]) == 2
    assert _envelope_error(capsys)["type"] == "JSONDecodeError"
    assert open(state).read() == "{not json"


def test_write_json_failure_keeps_the_old_file(monkeypatch, capsys, tmp_path):
    target = tmp_path / "game.json"
    target.write_text('{"old": 1}')

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cli.json, "dump", boom)
    assert cli._write_json(str(target), {"new": 2}, "goldfish-new") is False
    assert target.read_text() == '{"old": 1}'
    assert not (tmp_path / "game.json.tmp").exists()
    assert _envelope_error(capsys)["type"] == "OSError"
