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
