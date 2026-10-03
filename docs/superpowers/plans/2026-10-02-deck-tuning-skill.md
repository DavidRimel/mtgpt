# Deck-Tuning Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn mtgpt into a project skill that manages local deck projects and always tunes them through scan → research → tune, judged by a fixed scorecard.

**Architecture:** Four new pieces of code, each a focused module behind a CLI command: `projects.py` (deck folders under the git-ignored `decks/`), `scorecard.py` (reduce a goldfish run to the tuning targets, floor checks, keep/revert verdicts), card-impact and mulligan-cause tracking in the goldfish engine and runner, and `card-rule-merge`. The skill moves to `.claude/skills/mtgpt/` with a short workflow `SKILL.md` and on-demand reference files.

**Tech Stack:** Python ≥ 3.12, stdlib only at runtime, pytest. JSON CLI envelope `{"ok", "command", "data"|"error"}`.

**Spec:** `docs/superpowers/specs/2026-10-02-deck-tuning-skill-design.md`

## Global Constraints

- Python ≥ 3.12; no runtime dependencies (`dependencies = []` in `pyproject.toml` stays empty).
- Decks are local save files: nothing under `decks/` is ever committed; `decks/` and `*.goal.json` are already in `.gitignore`. Only code, docs, and `mtgpt/data/card_rules.json` are committed.
- Commander Spellbook is read-only and commander-scoped: the skill calls `card-combos "<commander>"` only. Nothing is submitted to any site.
- Every CLI command emits the existing JSON envelope through `cli._emit`; errors go through `api.error_payload`.
- Every module and test runs offline; tests use `tests/simdeck.py` builders and `tmp_path`, never the network.
- Target-round defaults: bracket 1–2 → 7, bracket 3 → 5, bracket 4 → 4, bracket 5 → 3.
- Scorecard numbers: keep margin 1.5 points (0.015), close-call band 3 points (0.03), confirm at 3000 games; guards on-curve −0.03, covered −0.03, opponent-win loss +0.02, mulligan +0.03, no color newly short. Levels: on-curve ≥ 0.70, covered ≥ 0.50, mulligan ≤ 0.25, untapped share ≥ 0.80.
- Checkpoint after 10 swaps tried or 3 consecutive non-keeps.
- Run the suite with `python3 -m pytest` from the repo root (pytest config adds `-q`).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A target round beyond the turn cap** (`target_round: 12` with `--turns 10`) must fail with a `GoalError` naming `target_round`, not an `IndexError`. → Task 6 test `test_target_round_beyond_turn_cap_is_a_goal_error`.
2. **Deck names with spaces and punctuation** ("Hapatra's Snakes!") must become a usable folder slug (`hapatra-s-snakes`), and a name with no letters or digits must be refused. → Task 7 tests `test_slugify_*`.
3. **Non-version files in a project folder** (`v8.goal.json`, `research.md`, `combos.json`, `v11b.txt`) must not confuse version listing or next-version naming. → Task 7 test `test_versions_ignore_other_files_and_count_suffixes`.
4. **A card seen in every game, or in none,** must give `win_delta: null`, never a division by zero. → Task 3 test `test_win_delta_is_none_without_both_groups`.
5. **Pilot game files written before this change** (no `used` / `mulligan_reasons` keys) must still load. → Task 2 test `test_game_files_from_before_tracking_still_load`.

---

### Task 1: `target_round` in the goal file

**Files:**
- Modify: `mtgpt/goal.py` (`_GOAL_FIELDS` at line 50, `Goal` dataclass at ~141, `load_goal` at ~158)
- Test: `tests/test_goal.py`

**Interfaces:**
- Produces: `Goal.target_round: int | None` (None when the goal file omits it). Goal files may contain `"target_round": <int ≥ 1>`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_goal.py`)

```python
def test_target_round_is_optional_and_validated():
    assert load({"archetype": "go_wide"}).target_round is None
    assert load({"archetype": "go_wide", "target_round": 5}).target_round == 5
    for bad in (0, -1, 2.5, "5", True):
        with pytest.raises(GoalError) as err:
            load({"archetype": "go_wide", "target_round": bad})
        assert err.value.field == "target_round"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_goal.py::test_target_round_is_optional_and_validated`
Expected: FAIL — `GoalError: unknown field(s): target_round`.

- [ ] **Step 3: Implement**

In `mtgpt/goal.py`, add the field name:

```python
_GOAL_FIELDS = ("archetype", "commander_turn", "engine", "thing", "win", "disruption",
                "opponent_win", "target_round")
```

Add the last field to `Goal` (after `opponent_win`):

```python
    opponent_win: OpponentWin | None = None
    #: Wins by this round are the tuning scorecard's primary metric; None means
    #: the bracket default (see scorecard.DEFAULT_TARGET_ROUND).
    target_round: int | None = None
```

In `load_goal`, after the `commander_turn` validation:

```python
    target_round = data.get("target_round")
    if target_round is not None and (
            not isinstance(target_round, int) or isinstance(target_round, bool) or target_round < 1):
        raise GoalError("target_round", "must be a whole number of rounds, 1 or more", [target_round])
```

and pass `target_round=target_round,` in the `Goal(...)` constructor call.

- [ ] **Step 4: Run the goal tests and the full suite**

Run: `python3 -m pytest tests/test_goal.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/goal.py tests/test_goal.py
git commit -m "feat(goal): optional target_round for the tuning scorecard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Engine records card use and mulligan reasons

**Files:**
- Modify: `mtgpt/goldfish/engine.py` — `GameState` (~line 112), `apply` (~451), `_play_land` (~483), `_cast` (~504), `_cast_free` (~530), `_mulligan` (~845), `_spend_answer` (~975), `to_dict` (~1403), `_from_dict` (~1477)
- Test: `tests/test_engine.py`

**Interfaces:**
- Produces: `GameState.used: dict[int, int]` — card index → table turn it was first cast, played as a land, or spent as an answer. `GameState.mulligan_reasons: list[str]` — one entry per mulligan taken, each `"few_lands"`, `"two_lands_no_ramp"`, or `"flood"`. Both serialized by `to_dict` as `"used": {str(idx): turn}` and `"mulligan_reasons": [...]`, and loaded by `from_dict` with defaults `{}` / `[]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_engine.py`; add imports at the top if missing: `import json`, `from mtgpt.goldfish.engine import _spend_answer, apply, from_dict, new_game, prepare, to_dict`, `from simdeck import NEVER, SOL_RING, SWORDS, deck, rigged`)

```python
NEVER_GOAL_T2 = {"archetype": "custom", "thing": "commander", "win": NEVER}


def test_casting_and_playing_record_first_use_turn():
    s = rigged(SOL_RING, hand=("Sol Ring", "Forest"))
    s = apply(s, {"play_land": "Forest"})
    s = apply(s, {"cast": "Sol Ring"})
    assert {s.cards[i].name: t for i, t in s.used.items()} == {"Forest": 1, "Sol Ring": 1}


def test_spending_an_answer_records_use():
    s = rigged(SWORDS, hand=("Swords to Plowshares",))
    idx = s.hand[0]
    _spend_answer(s, idx, due_now=True)
    assert s.used == {idx: s.turn}


def test_mulligans_record_why():
    s = new_game(prepare(deck(), NEVER_GOAL_T2), seed=1)  # 99 Forests: every hand floods
    assert s.mulligans >= 1
    assert s.mulligan_reasons == ["flood"] * s.mulligans


def test_used_and_mulligan_reasons_survive_serialization():
    s = rigged(SOL_RING, hand=("Sol Ring", "Forest"))
    s = apply(s, {"play_land": "Forest"})
    s.mulligan_reasons = ["flood"]
    back = from_dict(json.loads(json.dumps(to_dict(s))))
    assert back.used == s.used
    assert back.mulligan_reasons == ["flood"]


def test_game_files_from_before_tracking_still_load():
    data = to_dict(rigged(SOL_RING))
    del data["used"], data["mulligan_reasons"]
    back = from_dict(data)
    assert back.used == {} and back.mulligan_reasons == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_engine.py -k "use or mulligan or tracking"`
Expected: FAIL — `AttributeError: 'GameState' object has no attribute 'used'`.

- [ ] **Step 3: Implement**

In `GameState`, next to `mulligans: int = 0`:

```python
    mulligans: int = 0
    #: Why each mulligan was taken: "few_lands", "two_lands_no_ramp", "flood".
    mulligan_reasons: list[str] = field(default_factory=list)
    #: Card index -> table turn it was first cast, played, or spent as an answer.
    #: Feeds the per-card impact report.
    used: dict[int, int] = field(default_factory=dict)
```

Record use. In `apply`, the `play_land_top` branch, after `s.lands_played += 1`:

```python
        s.used.setdefault(idx, s.turn)
```

In `_play_land`, after `s.lands_played += 1`:

```python
    s.used.setdefault(idx, s.turn)
```

In `_cast`, right after `s.cast_names.append(name)`:

```python
    s.used.setdefault(idx, s.turn)
```

In `_cast_free`, right after `s.cast_names.append(card.name)`:

```python
    s.used.setdefault(idx, s.turn)
```

In `_spend_answer`, after `s.graveyard.append(idx)`:

```python
    s.used.setdefault(idx, s.turn)
```

Mulligan reasons. In `_mulligan`, record the reason before each mulligan:

```python
    while not _keepable(s) and 7 - s.mulligans >= 5:
        s.mulligan_reasons.append(_mulligan_reason(s))
        s.mulligans += 1
```

and add below `_keepable`:

```python
def _mulligan_reason(s: GameState) -> str:
    """Why `_keepable` rejected the hand, in the keep rule's own terms."""
    lands = sum(1 for i in s.hand if s.cards[i].is_land or s.cards[i].is_mdfc_land)
    if lands > 5:
        return "flood"
    if lands == 2:
        return "two_lands_no_ramp"
    return "few_lands"
```

Serialization. In `to_dict`, after `"mulligans": s.mulligans,`:

```python
        "mulligan_reasons": list(s.mulligan_reasons),
        "used": {str(k): v for k, v in s.used.items()},
```

In `_from_dict`, after the `plain["pending_tutor_source"] = ...` line:

```python
    plain["mulligan_reasons"] = data.get("mulligan_reasons", [])
    plain["used"] = {int(k): v for k, v in data.get("used", {}).items()}
```

- [ ] **Step 4: Run the engine tests and the full suite**

Run: `python3 -m pytest tests/test_engine.py && python3 -m pytest`
Expected: all pass. (The policy's look-ahead copies also record use; they are thrown away, so this is harmless.)

- [ ] **Step 5: Commit**

```bash
git add mtgpt/goldfish/engine.py tests/test_engine.py
git commit -m "feat(goldfish): record each card's first use and why each mulligan happened

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Runner reports mulligan causes and card impact

**Files:**
- Modify: `mtgpt/goldfish/run.py` — `simulate`, `summarize`, `_setup_block`; add `_card_impact_block`, `_win_delta`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `GameState.used`, `GameState.mulligan_reasons` (Task 2).
- Produces:
  - `report["setup"]["mulligan_causes"]: dict[str, float]` — share of games with at least one mulligan for that reason.
  - `simulate(deck, goal_raw, *, games, turn_cap, seed, disruption, impact_round: int | None = None)`; same keyword on `summarize`. When `impact_round` is set, `report["card_impact"]: dict[str, dict]` keyed by card name (basic lands and commanders excluded), each `{"seen_rate", "win_delta", "dead_rate", "cast_rate", "median_turn"}`.
  - `_win_delta(won_seen: int, seen: int, won_unseen: int, unseen: int) -> float | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_run.py`; extend the `simdeck` import with what is missing)

```python
from mtgpt.goldfish.run import _win_delta

UNCASTABLE = card("Uncastable Horror", "Creature — Horror", "", mana_cost="{20}", power=1.0)


def test_mulligan_causes_are_reported():
    report = simulate(deck(), NEVER_GOAL, games=10)
    assert report["setup"]["mulligan_causes"] == {"flood": 1.0}


def test_card_impact_only_when_asked():
    assert "card_impact" not in simulate(deck(SOL_RING), NEVER_GOAL, games=5)


def test_card_impact_reports_dead_and_cast_cards():
    report = simulate(deck(SOL_RING, UNCASTABLE, lands=97), NEVER_GOAL, games=80,
                      impact_round=5)
    impact = report["card_impact"]
    assert "Forest" not in impact and "Test Commander" not in impact
    assert impact["Uncastable Horror"]["seen_rate"] > 0
    assert impact["Uncastable Horror"]["dead_rate"] == 1.0
    assert impact["Uncastable Horror"]["median_turn"] is None
    assert impact["Sol Ring"]["cast_rate"] > 0.9
    assert impact["Sol Ring"]["median_turn"] is not None


def test_win_delta_is_none_without_both_groups():
    assert _win_delta(3, 10, 0, 0) is None
    assert _win_delta(0, 0, 3, 10) is None
    assert _win_delta(5, 10, 2, 10) == 0.3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_run.py -k "mulligan_causes or card_impact or win_delta"`
Expected: FAIL — `ImportError: cannot import name '_win_delta'`.

- [ ] **Step 3: Implement**

In `simulate`, add the keyword and pass it through:

```python
def simulate(deck: ResolvedDeck, goal_raw: dict, *, games: int = DEFAULT_GAMES,
             turn_cap: int = DEFAULT_TURN_CAP, seed: int = 1,
             disruption: bool = True, impact_round: int | None = None) -> dict:
    """Play `games` auto games and summarize them. `impact_round` adds the
    per-card impact block, judging wins by that round."""
    if games < 1:
        raise ValueError("games must be 1 or more")
    setup = prepare(deck, goal_raw)
    states = [play(setup, seed=f"{seed}-{i}", turn_cap=turn_cap, disruption=disruption)
              for i in range(games)]
    return summarize(setup, states, seed=seed, turn_cap=turn_cap, disruption=disruption,
                     impact_round=impact_round)
```

In `summarize`, add `impact_round: int | None = None` to the keyword-only parameters, build the dict into a local `report`, and before returning:

```python
    if impact_round is not None:
        report["card_impact"] = _card_impact_block(setup, states, impact_round)
    return report
```

In `_setup_block`, add to the returned dict after `"mulligan_rate"`:

```python
        "mulligan_causes": {k: _ratio(v, games) for k, v in sorted(
            Counter(r for s in states for r in set(s.mulligan_reasons)).items())},
```

Add after `_opponent_win_block`:

```python
def _card_impact_block(setup: Setup, states, impact_round: int) -> dict:
    """Per card: how often it was seen, whether games it was seen in were won
    more often (by `impact_round`), and how often it sat dead once seen.

    Seen means it left the library: drawn, tutored, or put onto the
    battlefield. Used means cast, played as a land, spent as an answer, or on
    the battlefield at the end. Basic lands and commanders are left out.
    """
    games = len(states)
    names = sorted({c.name for i, c in enumerate(setup.cards)
                    if not c.is_basic and i not in setup.commanders})
    rows = {n: {"seen": 0, "won_seen": 0, "unseen": 0, "won_unseen": 0, "used": 0, "turns": []}
            for n in names}
    for s in states:
        won = s.checkpoints["win"] is not None and s.checkpoints["win"] <= impact_round
        in_library = set(s.library)
        seen = {s.cards[i].name for i in range(len(s.cards))
                if i not in in_library and i not in setup.commanders}
        first_use: dict[str, int | None] = {}
        for idx, turn in s.used.items():
            name = s.cards[idx].name
            prior = first_use.get(name)
            first_use[name] = turn if prior is None else min(prior, turn)
        for perm in s.battlefield:
            if perm.card is not None:
                first_use.setdefault(s.cards[perm.card].name, None)
        for name in names:
            row = rows[name]
            if name in seen:
                row["seen"] += 1
                row["won_seen"] += won
                if name in first_use:
                    row["used"] += 1
                    if first_use[name] is not None:
                        row["turns"].append(first_use[name])
            else:
                row["unseen"] += 1
                row["won_unseen"] += won
    return {
        name: {
            "seen_rate": _ratio(r["seen"], games),
            "win_delta": _win_delta(r["won_seen"], r["seen"], r["won_unseen"], r["unseen"]),
            "dead_rate": _ratio(r["seen"] - r["used"], r["seen"]),
            "cast_rate": _ratio(r["used"], r["seen"]),
            "median_turn": _percentile(r["turns"], 0.5),
        }
        for name, r in rows.items()
    }


def _win_delta(won_seen: int, seen: int, won_unseen: int, unseen: int) -> float | None:
    """Win rate when seen minus win rate when not; None without both groups."""
    if not seen or not unseen:
        return None
    return round(won_seen / seen - won_unseen / unseen, 4)
```

- [ ] **Step 4: Run the runner tests and the full suite**

Run: `python3 -m pytest tests/test_run.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/goldfish/run.py tests/test_run.py
git commit -m "feat(goldfish): mulligan causes and an opt-in per-card impact report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `scorecard.py` — targets, land base, score, weaknesses, verdict

**Files:**
- Create: `mtgpt/scorecard.py`
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: goldfish report dicts (Task 3 shape); `audit.AuditReport.pips` (`PipReport.color/sources/required/ok`); `goldfish.engine.Setup.cards` (`CardInfo.is_land`, `.effect.enters_tapped`, `.effect.fetch_tapped`, `.unmodeled`, `.functions`, `.name`).
- Produces (all in `mtgpt/scorecard.py`):
  - `DEFAULT_TARGET_ROUND: dict[int, int]`, `LEVELS`, `GUARDS`, `KEEP_MARGIN = 0.015`, `CLOSE_CALL = 0.03`, `CONFIRM_GAMES = 3000`
  - `target_round(goal_raw: dict, bracket: int) -> int`
  - `land_base(setup: Setup, audit_report: AuditReport) -> dict` → `{"lands", "untapped_share", "colors": {c: {"sources","required","ok"}}, "short_colors": [..]}`
  - `score(report: dict, land: dict, round_: int) -> dict` → keys `target_round, primary, win_rate, on_curve, covered, answered, opponent_loss, protection_stopped, win_after_event, mulligan, mulligan_causes, color_screw, untapped_share, short_colors`
  - `weaknesses(sc: dict) -> list[dict]` — misses sorted worst first, each `{"target", "value", "level", "miss"}`; a short color adds `{"target": "short_colors", "value": [...], "level": [], "miss": 1.0}`
  - `verdict(before: dict, after: dict) -> dict` → `{"verdict": "keep"|"revert"|"mixed", "primary_delta", "broken_guards": [...], "close_call": bool}`
  - `mark_measurable(impact: dict, setup: Setup, library: dict | None = None) -> dict` — adds `"measurable": bool` to each row.

- [ ] **Step 1: Write the failing tests** — create `tests/test_scorecard.py`:

```python
import pytest

from mtgpt import scorecard as sc
from mtgpt.audit import audit
from mtgpt.goldfish.engine import prepare

from simdeck import BEAR, NEVER, SOL_RING, SWORDS, card, deck, forest

NEVER_GOAL = {"archetype": "custom", "thing": "commander", "win": NEVER}
TAPPED_LAND = card("Tapped Grove", "Land", "Tapped Grove enters tapped.\n{T}: Add {G}.",
                   produced_mana="G", colors="")


def fake_score(**over):
    base = {"target_round": 5, "primary": 0.30, "win_rate": 0.6, "on_curve": 0.72,
            "covered": 0.55, "answered": 0.8, "opponent_loss": 0.15,
            "protection_stopped": 0.3, "win_after_event": 0.4, "mulligan": 0.20,
            "mulligan_causes": {}, "color_screw": 0.05, "untapped_share": 0.85,
            "short_colors": []}
    return base | over


def test_target_round_defaults_by_bracket_and_goal_wins():
    assert [sc.target_round({}, b) for b in (1, 2, 3, 4, 5)] == [7, 7, 5, 4, 3]
    assert sc.target_round({"target_round": 6}, 3) == 6


def test_land_base_counts_untapped_share_and_short_colors():
    d = deck(TAPPED_LAND, lands=98)  # 98 Forests + 1 tapped land = 99 lands
    setup = prepare(d, NEVER_GOAL)
    land = sc.land_base(setup, audit(d))
    assert land["lands"] == 99
    assert land["untapped_share"] == round(98 / 99, 4)
    assert land["short_colors"] == []


def test_score_reads_the_report():
    report = {
        "win": {"win_by_round": [0, 0, 0, 0.1, 0.25, 0.4], "win_rate": 0.5},
        "commander": {"on_curve_rate": 0.7, "late_reasons": {"color_screw": 0.1}},
        "thing": {"covered_rate": 0.5},
        "opponent_win": {"answered_rate": 0.8},
        "loss": {"by_reason": {"opponent_win": 0.12}},
        "disruption": {"stopped_by_protection_rate": 0.3, "win_rate_after_event": 0.4},
        "setup": {"mulligan_rate": 0.2, "mulligan_causes": {"few_lands": 0.1}},
    }
    land = {"untapped_share": 0.9, "short_colors": ["G"]}
    s = sc.score(report, land, 5)
    assert s["primary"] == 0.25 and s["opponent_loss"] == 0.12
    assert s["color_screw"] == 0.1 and s["short_colors"] == ["G"]


def test_weaknesses_lists_misses_worst_first():
    s = fake_score(on_curve=0.50, mulligan=0.30, short_colors=["U"])
    names = [w["target"] for w in sc.weaknesses(s)]
    assert names[0] == "short_colors"
    assert names[1:] == ["on_curve", "mulligan"]
    assert sc.weaknesses(fake_score()) == []


def test_verdict_keep():
    v = sc.verdict(fake_score(), fake_score(primary=0.33))
    assert v["verdict"] == "keep" and v["primary_delta"] == 0.03 and not v["close_call"]


def test_verdict_revert_on_primary_drop():
    assert sc.verdict(fake_score(), fake_score(primary=0.28))["verdict"] == "revert"


def test_verdict_revert_on_broken_guard():
    v = sc.verdict(fake_score(), fake_score(primary=0.34, on_curve=0.65))
    assert v["verdict"] == "revert"
    assert [g["guard"] for g in v["broken_guards"]] == ["on_curve"]


def test_verdict_revert_on_new_short_color():
    v = sc.verdict(fake_score(), fake_score(primary=0.34, short_colors=["G"]))
    assert v["verdict"] == "revert"
    assert v["broken_guards"][0]["guard"] == "short_colors"


def test_verdict_mixed_and_close_call():
    v = sc.verdict(fake_score(), fake_score(primary=0.31))
    assert v["verdict"] == "mixed" and v["close_call"]


def test_mark_measurable():
    d = deck(SWORDS, BEAR, SOL_RING)
    setup = prepare(d, NEVER_GOAL)
    impact = {"Swords to Plowshares": {}, "Grizzly Bears": {}, "Sol Ring": {}}
    library = {"Sol Ring": {"status": "ignored", "note": "x"}}
    out = sc.mark_measurable(impact, setup, library)
    assert out["Swords to Plowshares"]["measurable"] is False  # removal: value unseen by a goldfish
    assert out["Sol Ring"]["measurable"] is False              # library says ignored
    assert out["Grizzly Bears"]["measurable"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_scorecard.py`
Expected: FAIL — `ImportError: cannot import name 'scorecard'`.

- [ ] **Step 3: Implement** — create `mtgpt/scorecard.py`:

```python
# mtgpt/scorecard.py
"""Reduce a goldfish run to the tuning targets, and judge one version against another.

The tuning loop asks one question after every swap: is the new version better?
Answering it by eye drifts from session to session, so the answer lives here as
fixed numbers: target levels for naming the weakest area, and guard tolerances
for the keep / revert / mixed verdict. Change the numbers here and only here.
"""

from __future__ import annotations

from . import card_rules
from .audit import AuditReport
from .goldfish.engine import Setup

#: Wins by this round are the primary metric when the goal names none.
DEFAULT_TARGET_ROUND = {1: 7, 2: 7, 3: 5, 4: 4, 5: 3}

#: Target levels that name a weakness: metric -> (direction, level).
LEVELS = {
    "on_curve": (">=", 0.70),
    "covered": (">=", 0.50),
    "mulligan": ("<=", 0.25),
    "untapped_share": (">=", 0.80),
}

#: How far a metric may move the wrong way before a swap is reverted.
GUARDS = {
    "on_curve": -0.03,
    "covered": -0.03,
    "opponent_loss": 0.02,
    "mulligan": 0.03,
}

#: The primary metric must rise this much for a clean keep.
KEEP_MARGIN = 0.015
#: A primary change smaller than this is re-run at CONFIRM_GAMES before it counts.
CLOSE_CALL = 0.03
CONFIRM_GAMES = 3000

#: Functions whose value a goldfish cannot see beyond answering opponent wins.
NOT_MEASURABLE_FUNCTIONS = frozenset({"spot_removal", "sweeper", "counterspell"})


def target_round(goal_raw: dict, bracket: int) -> int:
    return goal_raw.get("target_round") or DEFAULT_TARGET_ROUND[bracket]


def land_base(setup: Setup, audit_report: AuditReport) -> dict:
    """Share of lands entering untapped, and each color's sources against its pips."""
    lands = [c for c in setup.cards if c.is_land]
    untapped = [c for c in lands if not (c.effect.enters_tapped or c.effect.fetch_tapped)]
    colors = {p.color: {"sources": p.sources, "required": p.required, "ok": p.ok}
              for p in audit_report.pips}
    return {
        "lands": len(lands),
        "untapped_share": _ratio(len(untapped), len(lands)),
        "colors": colors,
        "short_colors": sorted(c for c, v in colors.items() if not v["ok"]),
    }


def score(report: dict, land: dict, round_: int) -> dict:
    """The tuning targets, read from one goldfish report and its land base."""
    by_round = report["win"]["win_by_round"]
    return {
        "target_round": round_,
        "primary": by_round[round_ - 1] if len(by_round) >= round_ else None,
        "win_rate": report["win"]["win_rate"],
        "on_curve": report["commander"]["on_curve_rate"],
        "covered": report["thing"]["covered_rate"],
        "answered": report["opponent_win"]["answered_rate"],
        "opponent_loss": report["loss"]["by_reason"].get("opponent_win", 0.0),
        "protection_stopped": report["disruption"]["stopped_by_protection_rate"],
        "win_after_event": report["disruption"]["win_rate_after_event"],
        "mulligan": report["setup"]["mulligan_rate"],
        "mulligan_causes": report["setup"]["mulligan_causes"],
        "color_screw": report["commander"]["late_reasons"].get("color_screw", 0.0),
        "untapped_share": land["untapped_share"],
        "short_colors": land["short_colors"],
    }


def weaknesses(sc: dict) -> list[dict]:
    """Every target missing its level, worst first. A short color leads."""
    out = []
    for metric, (direction, level) in LEVELS.items():
        value = sc.get(metric)
        if value is None:
            continue
        miss = level - value if direction == ">=" else value - level
        if miss > 0:
            out.append({"target": metric, "value": value, "level": level, "miss": round(miss, 4)})
    out.sort(key=lambda w: -w["miss"])
    if sc.get("short_colors"):
        out.insert(0, {"target": "short_colors", "value": sc["short_colors"], "level": [],
                       "miss": 1.0})
    return out


def verdict(before: dict, after: dict) -> dict:
    """keep, revert, or mixed — see the module docstring and GUARDS."""
    delta = _sub(after["primary"], before["primary"])
    broken = []
    for metric, tolerance in GUARDS.items():
        moved = _sub(after.get(metric), before.get(metric))
        if moved is None:
            continue
        if (tolerance < 0 and moved < tolerance) or (tolerance > 0 and moved > tolerance):
            broken.append({"guard": metric, "before": before[metric], "after": after[metric],
                           "tolerance": tolerance})
    new_short = sorted(set(after["short_colors"]) - set(before["short_colors"]))
    if new_short:
        broken.append({"guard": "short_colors", "before": before["short_colors"],
                       "after": after["short_colors"], "tolerance": []})
    if delta is None:
        result = "mixed"
    elif delta < 0 or broken:
        result = "revert"
    elif delta >= KEEP_MARGIN:
        result = "keep"
    else:
        result = "mixed"
    return {"verdict": result, "primary_delta": delta, "broken_guards": broken,
            "close_call": delta is not None and abs(delta) < CLOSE_CALL}


def mark_measurable(impact: dict, setup: Setup, library: dict | None = None) -> dict:
    """Flag cards whose worth a goldfish cannot see, so they are not cut as dead."""
    library = card_rules.load()["cards"] if library is None else library
    info = {c.name: c for c in setup.cards}
    for name, row in impact.items():
        card = info[name]
        entry = library.get(name)
        row["measurable"] = not (
            card.unmodeled
            or (entry is not None and entry["status"] == "ignored")
            or bool(NOT_MEASURABLE_FUNCTIONS & set(card.functions)))
    return impact


def _sub(after, before):
    if after is None or before is None:
        return None
    return round(after - before, 4)


def _ratio(n, total):
    return round(n / total, 4) if total else None
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_scorecard.py && python3 -m pytest`
Expected: all pass. If `test_land_base_counts_untapped_share_and_short_colors` fails because `TAPPED_LAND` does not parse as entering tapped, print `prepare(d, NEVER_GOAL).cards` for it and adjust the oracle text to the templating `effects._SELF_ENTERS_TAPPED` matches (read that regex in `mtgpt/effects.py`); do not change the regex.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/scorecard.py tests/test_scorecard.py
git commit -m "feat(scorecard): tuning targets, land base, weaknesses, keep/revert verdict

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Floors — reject a swap before it is simmed

**Files:**
- Modify: `mtgpt/scorecard.py` (add `floors`, `_assembled`)
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: `classify.classify_deck`, `audit.audit` (`AuditReport.categories`: `CategoryCount.function/count/target_min`), `brackets.check(deck, tags, target, combos)` → `BracketReport.findings/game_changers`, `models.Severity`.
- Produces: `floors(before: ResolvedDeck, after: ResolvedDeck, bracket: int, combos=()) -> dict` → `{"ok": bool, "rejected": [str], "warnings": [str]}`. `combos` is the `"combos"` list from `card-combos` output (dicts with `"cards"` and `"card_count"`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scorecard.py`; add `import dataclasses` at the top)

```python
def removal(i):
    return card(f"Removal {i}", "Instant", "Destroy target creature.", mana_cost="{G}")


def gc(i):
    return dataclasses.replace(card(f"Changer {i}", "Artifact", "", mana_cost="{2}"),
                               is_game_changer=True)


COMBO_PIECE = card("Combo Piece", "Artifact", "", mana_cost="{2}")
COMBOS = [{"cards": ["Test Commander", "Combo Piece"], "card_count": 2}]


def test_floors_reject_dropping_removal_below_its_band():
    before = deck(*[removal(i) for i in range(5)], lands=37)
    after = deck(*[removal(i) for i in range(4)], BEAR, lands=37)
    result = sc.floors(before, after, 3)
    assert not result["ok"]
    assert "spot_removal" in result["rejected"][0]


def test_floors_allow_a_deck_already_below_that_gets_no_worse():
    before = deck(*[removal(i) for i in range(3)], BEAR, lands=37)
    after = deck(*[removal(i) for i in range(3)], SOL_RING, lands=37)
    assert sc.floors(before, after, 3)["ok"]


def test_floors_reject_a_fourth_game_changer_at_bracket_three():
    before = deck(*[gc(i) for i in range(3)], BEAR, lands=37)
    after = deck(*[gc(i) for i in range(4)], lands=37)
    result = sc.floors(before, after, 3)
    assert not result["ok"] and "Game Changers" in result["rejected"][0]


def test_floors_two_card_combo_rejected_at_bracket_two_warned_at_three():
    before = deck(BEAR, lands=37)
    after = deck(COMBO_PIECE, lands=37)
    assert not sc.floors(before, after, 2, COMBOS)["ok"]
    result = sc.floors(before, after, 3, COMBOS)
    assert result["ok"] and "two-card" in result["warnings"][0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_scorecard.py -k floors`
Expected: FAIL — `AttributeError: module 'mtgpt.scorecard' has no attribute 'floors'`.

- [ ] **Step 3: Implement** — add to `mtgpt/scorecard.py` (imports at the top: `from .audit import AuditReport, audit`, `from .brackets import check`, `from .classify import classify_deck`, `from .models import ResolvedDeck, Severity`):

```python
def floors(before: ResolvedDeck, after: ResolvedDeck, bracket: int, combos=()) -> dict:
    """Reasons to reject `after` without simming it.

    A category (lands, ramp, draw, removal, wipes, protection) may not drop
    below its audit band's minimum — unless it was already there and the swap
    does not lower it further. A bracket error `before` did not have rejects;
    a new bracket warning (a two-card combo at bracket 3) is passed through.
    `combos` are the commander's Spellbook combos cached at research time.
    """
    tags_before, tags_after = classify_deck(before), classify_deck(after)
    counts_before = {c.function: c.count for c in audit(before, tags_before).categories}
    rejected, warnings = [], []
    for cat in audit(after, tags_after).categories:
        was = counts_before[cat.function]
        if cat.count < cat.target_min and cat.count < was:
            rejected.append(f"{cat.function.value}: {cat.count} is below the minimum "
                            f"{cat.target_min} (was {was})")
    old = check(before, tags_before, target=bracket, combos=_assembled(combos, before))
    new = check(after, tags_after, target=bracket, combos=_assembled(combos, after))
    old_messages = {f.message for f in old.findings}
    for finding in new.findings:
        if finding.code == "game_changers":
            if len(new.game_changers) <= len(old.game_changers):
                continue
        elif finding.message in old_messages:
            continue
        (rejected if finding.severity is Severity.ERROR else warnings).append(finding.message)
    return {"ok": not rejected, "rejected": rejected, "warnings": warnings}


def _assembled(combos, deck: ResolvedDeck) -> tuple[dict, ...]:
    """The cached combos whose every piece is in `deck`, commander included."""
    present = ({c.name.casefold() for c in deck.commanders}
               | {c.name.casefold() for _, c in deck.cards})
    return tuple(c for c in combos
                 if c.get("cards") and all(n.casefold() in present for n in c["cards"]))
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_scorecard.py && python3 -m pytest`
Expected: all pass. If the removal tests fail because "Destroy target creature." is not tagged `spot_removal`, run `python3 -c "from mtgpt.classify import classify; from simdeck import card; ..."` from `tests/` to see the tags and use the oracle text of a real removal spell the classifier tags (e.g. Murder's "Destroy target creature." or Swords' text from `simdeck.SWORDS`).

- [ ] **Step 5: Commit**

```bash
git add mtgpt/scorecard.py tests/test_scorecard.py
git commit -m "feat(scorecard): floors reject swaps that thin a category or break the bracket

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `run_scorecard`, the API, and the `scorecard` command

**Files:**
- Modify: `mtgpt/scorecard.py` (add `run_scorecard`), `mtgpt/api.py` (add `scorecard`), `mtgpt/cli.py` (add the `scorecard` subcommand and dispatch; update the module docstring's command list)
- Test: `tests/test_scorecard.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `goldfish.run.simulate(..., impact_round=)` (Task 3), `land_base`, `score`, `weaknesses`, `verdict`, `mark_measurable`, `floors` (Tasks 4–5), `audit.audit`, `goal.GoalError(field, message, values)`.
- Produces:
  - `scorecard.run_scorecard(decks: list[ResolvedDeck], goal: dict, *, bracket: int, combos=(), games: int = 1000, turns: int = 10, seed: int = 1, disruption: bool = True, confirm_games: int = CONFIRM_GAMES) -> dict`.
    One deck → `{"target_round", "games", "score", "weaknesses", "card_impact"}`.
    Two decks (best, candidate) → `{"target_round", "games", "floors", "before", "after", "verdict", "weaknesses", "card_impact"}`; when floors fail, `"verdict": {"verdict": "rejected", ...}` and no sim runs; when the first verdict is a close call, both are re-run at `confirm_games` and `"games"` reports that number.
  - `api.scorecard(texts: list[str], goal: dict, *, bracket: int, combos=None, games, turns, seed, disruption, client) -> dict`.
  - CLI: `scorecard --file A [--file B] --goal G --bracket N [--combos PATH] [--games N] [--turns N] [--seed N] [--no-disruption]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scorecard.py`:

```python
from mtgpt.goal import GoalError


def test_run_scorecard_one_deck():
    out = sc.run_scorecard([deck(SOL_RING, BEAR, lands=37)], NEVER_GOAL, bracket=3, games=10)
    assert out["target_round"] == 5 and out["games"] == 10
    assert "primary" in out["score"] and "Sol Ring" in out["card_impact"]
    assert "measurable" in out["card_impact"]["Sol Ring"]


def test_run_scorecard_same_deck_is_a_close_call_confirmed_at_more_games():
    d = deck(SOL_RING, BEAR, lands=37)
    out = sc.run_scorecard([d, d], NEVER_GOAL, bracket=3, games=10, confirm_games=20)
    assert out["verdict"]["primary_delta"] == 0.0
    assert out["games"] == 20


def test_run_scorecard_rejects_on_floors_without_simming():
    before = deck(*[removal(i) for i in range(5)], lands=37)
    after = deck(*[removal(i) for i in range(4)], BEAR, lands=37)
    out = sc.run_scorecard([before, after], NEVER_GOAL, bracket=3, games=10)
    assert out["verdict"]["verdict"] == "rejected"
    assert "after" not in out


def test_target_round_beyond_turn_cap_is_a_goal_error():
    with pytest.raises(GoalError) as err:
        sc.run_scorecard([deck()], NEVER_GOAL | {"target_round": 12}, bracket=3, games=5, turns=10)
    assert err.value.field == "target_round"
```

Append to `tests/test_cli.py` (and add `"scorecard"` to the set in `test_every_subcommand_is_registered`):

```python
def test_scorecard_passes_files_goal_and_bracket(monkeypatch, capsys, tmp_path):
    a, b, goal = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "g.json"
    a.write_text("1 Sol Ring\n"); b.write_text("1 Mind Stone\n")
    goal.write_text('{"archetype": "go_wide"}')
    seen = {}

    def fake(texts, goal_raw, **kw):
        seen.update(texts=texts, goal=goal_raw, **kw)
        return {"verdict": {"verdict": "keep"}}

    monkeypatch.setattr(cli.api, "scorecard", fake)
    assert cli.main(["scorecard", "--file", str(a), "--file", str(b), "--goal", str(goal),
                     "--bracket", "4", "--games", "50"]) == 0
    assert seen["texts"] == ["1 Sol Ring\n", "1 Mind Stone\n"]
    assert seen["bracket"] == 4 and seen["games"] == 50 and seen["combos"] is None
    assert json.loads(capsys.readouterr().out)["data"]["verdict"]["verdict"] == "keep"


def test_scorecard_reads_a_saved_card_combos_envelope(monkeypatch, capsys, tmp_path):
    a, goal, combos = tmp_path / "a.txt", tmp_path / "g.json", tmp_path / "combos.json"
    a.write_text("1 Sol Ring\n"); goal.write_text('{"archetype": "go_wide"}')
    combos.write_text(json.dumps({"ok": True, "command": "card-combos", "data": {
        "card": "X", "count": 1, "combos": [{"cards": ["X", "Y"], "card_count": 2}]}}))
    seen = {}
    monkeypatch.setattr(cli.api, "scorecard", lambda texts, g, **kw: seen.update(kw) or {})
    assert cli.main(["scorecard", "--file", str(a), "--goal", str(goal), "--bracket", "3",
                     "--combos", str(combos)]) == 0
    assert seen["combos"] == [{"cards": ["X", "Y"], "card_count": 2}]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_scorecard.py -k run_scorecard tests/test_cli.py -k scorecard`
Expected: FAIL — no `run_scorecard`; CLI `invalid choice: 'scorecard'`.

- [ ] **Step 3: Implement**

Add to `mtgpt/scorecard.py` (imports: `from .goal import GoalError`, `from .goldfish.engine import DEFAULT_TURN_CAP, Setup, prepare`, `from .goldfish.run import DEFAULT_GAMES, simulate`):

```python
def run_scorecard(decks: list[ResolvedDeck], goal: dict, *, bracket: int, combos=(),
                  games: int = DEFAULT_GAMES, turns: int = DEFAULT_TURN_CAP, seed: int = 1,
                  disruption: bool = True, confirm_games: int = CONFIRM_GAMES) -> dict:
    """Score one deck, or judge a candidate (second) against the best (first)."""
    round_ = target_round(goal, bracket)
    if round_ > turns:
        raise GoalError("target_round",
                        f"target round {round_} is beyond the {turns}-turn cap; raise --turns",
                        [round_])

    def run(deck: ResolvedDeck, n: int):
        report = simulate(deck, goal, games=n, turn_cap=turns, seed=seed,
                          disruption=disruption, impact_round=round_)
        setup = prepare(deck, goal)
        return (score(report, land_base(setup, audit(deck)), round_),
                mark_measurable(report["card_impact"], setup))

    if len(decks) == 1:
        s, impact = run(decks[0], games)
        return {"target_round": round_, "games": games, "score": s,
                "weaknesses": weaknesses(s), "card_impact": impact}

    before_deck, after_deck = decks
    gate = floors(before_deck, after_deck, bracket, combos)
    if not gate["ok"]:
        return {"target_round": round_, "games": 0, "floors": gate,
                "verdict": {"verdict": "rejected", "primary_delta": None,
                            "broken_guards": [], "close_call": False}}
    n = games
    before, _ = run(before_deck, n)
    after, impact = run(after_deck, n)
    judged = verdict(before, after)
    if judged["close_call"] and n < confirm_games:
        n = confirm_games
        before, _ = run(before_deck, n)
        after, impact = run(after_deck, n)
        judged = verdict(before, after)
    return {"target_round": round_, "games": n, "floors": gate, "before": before,
            "after": after, "verdict": judged, "weaknesses": weaknesses(after),
            "card_impact": impact}
```

Add to `mtgpt/api.py` in the Goldfish section (after `goldfish_compare`):

```python
def scorecard(
    texts: list[str],
    goal: dict,
    *,
    bracket: int,
    combos: list[dict] | None = None,
    games: int = DEFAULT_GAMES,
    turns: int = DEFAULT_TURN_CAP,
    seed: int = 1,
    disruption: bool = True,
    client: ScryfallClient | None = None,
) -> dict:
    """The tuning scorecard for one deck, or a verdict on a candidate against
    the best version (texts = [best, candidate])."""
    from .scorecard import run_scorecard

    scry = _client(client)
    decks = [_resolved(t, scry) for t in texts]
    result = run_scorecard(decks, goal, bracket=bracket, combos=combos or (), games=games,
                           turns=turns, seed=seed, disruption=disruption)
    result["warnings"] = _violations(validate(decks[-1]))
    return result
```

In `mtgpt/cli.py` `build_parser`, after the `goldfish-compare` parser:

```python
    score_cmd = sub.add_parser(
        "scorecard", help="Tuning targets for a deck, or keep/revert for a candidate")
    score_cmd.add_argument(
        "--file", action="append", required=True,
        help="Once to score a deck; twice to judge the second against the first")
    score_cmd.add_argument("--bracket", type=int, required=True, choices=[1, 2, 3, 4, 5])
    score_cmd.add_argument("--combos", help="The commander's cached card-combos JSON")
    _add_goldfish_options(score_cmd, games=True)
```

Add a handler next to `_card_rule`:

```python
def _scorecard(args, command: str, client) -> int:
    if len(args.file) not in (1, 2):
        _emit(command, {"type": "MissingInput",
                        "message": "Pass --file once (score) or twice (best, then candidate)."},
              ok=False)
        return EXIT_USER_ERROR
    goal = _read_json(args.goal, command)
    if goal is _FAILED:
        return EXIT_USER_ERROR
    combos = None
    if args.combos:
        cached = _read_json(args.combos, command)
        if cached is _FAILED:
            return EXIT_USER_ERROR
        # `card-combos ... > combos.json` saves the whole envelope; accept that
        # or a bare {"combos": [...]}.
        combos = cached.get("data", cached).get("combos", [])
    texts = [_read_text_file(path, command) for path in args.file]
    if None in texts:
        return EXIT_USER_ERROR
    _emit(command, api.scorecard(texts, goal, bracket=args.bracket, combos=combos,
                                 games=args.games, turns=args.turns, seed=args.seed,
                                 disruption=not args.no_disruption, client=client))
    return EXIT_OK
```

In `main`, before `elif command.startswith("goldfish"):` add:

```python
        elif command == "scorecard":
            return _scorecard(args, command, client)
```

Add to the module docstring's command list:

```
    python3 -m mtgpt.cli scorecard        --file best.txt [--file candidate.txt] --goal goal.json --bracket 3
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_scorecard.py tests/test_cli.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/scorecard.py mtgpt/api.py mtgpt/cli.py tests/test_scorecard.py tests/test_cli.py
git commit -m "feat: scorecard command — targets, floors, close-call confirmation, card impact

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `projects.py` — deck projects on disk

**Files:**
- Create: `mtgpt/projects.py`
- Test: `tests/test_projects.py`

**Interfaces:**
- Consumes: `deckparse.parse(text).commanders` (entries with `.name`), `errors.MtgptError`.
- Produces (all in `mtgpt/projects.py`; every function takes `root: Path` first):
  - `DEFAULT_ROOT: Path` (repo `decks/`), `STAGES = ("scan", "research", "tune", "finish")`, `class ProjectError(MtgptError)`
  - `slugify(name: str) -> str`
  - `version_number(name: str) -> int | None` (`"v11b"` → 11; `"v8.goal"` → None)
  - `versions(root, slug) -> list[str]` sorted by number then suffix, from `v*.txt` files
  - `create(root, name, text, *, bracket, source=None) -> dict` → the project dict
  - `load(root, slug) -> dict`; `list_projects(root) -> list[dict]`
  - `status(root, slug) -> dict` → `{"project", "versions", "log_tail", "playtest_notes"}`
  - `save(root, slug, text, *, note="") -> dict` → `{"version", "path"}`
  - `set_best(root, slug, version, *, primary=None) -> dict`; `set_stage(root, slug, stage) -> dict`
  - `note(root, slug, text) -> None` (dated `### Playtest` entry); `log(root, slug, text) -> None` (append markdown)
- `project.json` keys: `name, slug, commander, bracket, source, best, best_primary, stage, created, updated`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_projects.py`:

```python
import json

import pytest

from mtgpt import projects
from mtgpt.projects import ProjectError

LIST = "Commander\n1 Hapatra, Vizier of Poisons\n\nDeck\n1 Sol Ring\n98 Swamp\n"


def test_slugify_handles_punctuation_and_spaces():
    assert projects.slugify("Hapatra's Snakes!") == "hapatra-s-snakes"
    assert projects.slugify("  Jodah  B4 ") == "jodah-b4"


def test_slugify_refuses_a_name_without_letters_or_digits():
    with pytest.raises(ProjectError):
        projects.slugify("!!!")


def test_create_writes_project_v1_and_log(tmp_path):
    p = projects.create(tmp_path, "Hapatra", LIST, bracket=3, source="https://x")
    folder = tmp_path / "hapatra"
    assert (folder / "v1.txt").read_text() == LIST
    assert json.loads((folder / "project.json").read_text()) == p
    assert p["commander"] == "Hapatra, Vizier of Poisons"
    assert p["best"] == "v1" and p["stage"] == "scan" and p["bracket"] == 3
    assert (folder / "log.md").read_text().startswith("# Hapatra")


def test_create_refuses_an_existing_project(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    with pytest.raises(ProjectError, match="project status"):
        projects.create(tmp_path, "Hapatra", LIST, bracket=3)


def test_versions_ignore_other_files_and_count_suffixes(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    folder = tmp_path / "hapatra"
    for name in ("v2.txt", "v11b.txt", "v4a.txt", "v8.goal.json", "research.md", "combos.json"):
        (folder / name).write_text("x")
    assert projects.versions(tmp_path, "hapatra") == ["v1", "v2", "v4a", "v11b"]
    assert projects.save(tmp_path, "hapatra", LIST)["version"] == "v12"


def test_save_logs_and_set_best_records_primary(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    saved = projects.save(tmp_path, "hapatra", LIST, note="+Blowfly -Bear")
    assert saved["version"] == "v2"
    assert "## v2 saved" in (tmp_path / "hapatra" / "log.md").read_text()
    p = projects.set_best(tmp_path, "hapatra", "v2", primary=0.277)
    assert p["best"] == "v2" and p["best_primary"] == 0.277


def test_set_best_refuses_a_missing_version(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    with pytest.raises(ProjectError):
        projects.set_best(tmp_path, "hapatra", "v9")


def test_stage_must_be_known(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    assert projects.set_stage(tmp_path, "hapatra", "tune")["stage"] == "tune"
    with pytest.raises(ProjectError):
        projects.set_stage(tmp_path, "hapatra", "done")


def test_list_and_status(tmp_path):
    projects.create(tmp_path, "Hapatra", LIST, bracket=3)
    projects.create(tmp_path, "Jodah B4", LIST, bracket=4)
    (tmp_path / "stray.txt").write_text("not a project")
    assert [p["slug"] for p in projects.list_projects(tmp_path)] == ["hapatra", "jodah-b4"]
    projects.note(tmp_path, "hapatra", "flooded twice")
    projects.log(tmp_path, "hapatra", "v2 vs v1: keep (+3.1)")
    st = projects.status(tmp_path, "hapatra")
    assert st["versions"] == ["v1"]
    assert any("flooded twice" in line for line in st["playtest_notes"])
    assert "v2 vs v1: keep (+3.1)" in st["log_tail"]


def test_load_of_a_missing_project_is_a_project_error(tmp_path):
    with pytest.raises(ProjectError, match="project list"):
        projects.load(tmp_path, "nope")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_projects.py`
Expected: FAIL — `ImportError: cannot import name 'projects'`.

- [ ] **Step 3: Implement** — create `mtgpt/projects.py`:

```python
# mtgpt/projects.py
"""Deck projects: one folder per deck under decks/, which git ignores.

Decks are a user's save files. Nothing here is committed: a project holds the
deck's versions (v1.txt, v2.txt, ... — each written once, never edited), its
goal file, research notes, cached combos, and a tuning log. Only card rules,
which every deck reuses, belong in the repo.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

from .deckparse import parse
from .errors import MtgptError

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "decks"
STAGES = ("scan", "research", "tune", "finish")
_VERSION = re.compile(r"^v(\d+)([a-z]?)$")
LOG_TAIL_LINES = 40


class ProjectError(MtgptError):
    """A deck project that does not exist, already exists, or is asked something invalid."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ProjectError(f"{name!r} has no letters or digits to name a deck folder with")
    return slug


def version_number(name: str) -> int | None:
    match = _VERSION.match(name)
    return int(match.group(1)) if match else None


def versions(root: Path, slug: str) -> list[str]:
    folder = _folder(root, slug)
    names = [p.stem for p in folder.glob("v*.txt") if _VERSION.match(p.stem)]
    return sorted(names, key=lambda n: (version_number(n), n))


def create(root: Path, name: str, text: str, *, bracket: int, source: str | None = None) -> dict:
    slug = slugify(name)
    folder = Path(root) / slug
    if (folder / "project.json").exists():
        raise ProjectError(f"a project named {slug!r} already exists; use `project status {slug}`")
    commanders = [e.name for e in parse(text).commanders]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "v1.txt").write_text(text, encoding="utf-8")
    today = _today()
    project = {"name": name, "slug": slug, "commander": " + ".join(commanders) or None,
               "bracket": bracket, "source": source, "best": "v1", "best_primary": None,
               "stage": "scan", "created": today, "updated": today}
    _write(folder, project)
    (folder / "log.md").write_text(f"# {name} — tuning log\n\n## v1 created {today}\n",
                                   encoding="utf-8")
    return project


def load(root: Path, slug: str) -> dict:
    path = Path(root) / slug / "project.json"
    if not path.exists():
        raise ProjectError(f"no deck project {slug!r}; run `project list` to see them")
    return json.loads(path.read_text(encoding="utf-8"))


def list_projects(root: Path) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(root.glob("*/project.json"))]


def status(root: Path, slug: str) -> dict:
    project = load(root, slug)
    lines = (Path(root) / slug / "log.md").read_text(encoding="utf-8").splitlines()
    notes, in_note = [], False
    for line in lines:
        if line.startswith("### Playtest"):
            in_note = True
        elif line.startswith("#"):
            in_note = False
        if in_note and line.strip():
            notes.append(line)
    return {"project": project, "versions": versions(root, slug),
            "log_tail": "\n".join(lines[-LOG_TAIL_LINES:]), "playtest_notes": notes}


def save(root: Path, slug: str, text: str, *, note: str = "") -> dict:
    load(root, slug)
    numbers = [version_number(v) for v in versions(root, slug)]
    version = f"v{max(numbers, default=0) + 1}"
    path = Path(root) / slug / f"{version}.txt"
    path.write_text(text, encoding="utf-8")
    log(root, slug, f"## {version} saved {_today()}\n{note}".rstrip())
    _touch(root, slug)
    return {"version": version, "path": str(path)}


def set_best(root: Path, slug: str, version: str, *, primary: float | None = None) -> dict:
    project = load(root, slug)
    if version not in versions(root, slug):
        raise ProjectError(f"{slug} has no version {version!r}")
    project.update(best=version, best_primary=primary, updated=_today())
    _write(Path(root) / slug, project)
    return project


def set_stage(root: Path, slug: str, stage: str) -> dict:
    if stage not in STAGES:
        raise ProjectError(f"stage must be one of {', '.join(STAGES)}, got {stage!r}")
    project = load(root, slug)
    project.update(stage=stage, updated=_today())
    _write(Path(root) / slug, project)
    return project


def note(root: Path, slug: str, text: str) -> None:
    """A note from a real game, read first at the start of the next session."""
    log(root, slug, f"### Playtest {_today()}\n{text}")


def log(root: Path, slug: str, text: str) -> None:
    load(root, slug)
    with open(Path(root) / slug / "log.md", "a", encoding="utf-8") as handle:
        handle.write(f"\n{text}\n")
    _touch(root, slug)


def _folder(root: Path, slug: str) -> Path:
    load(root, slug)
    return Path(root) / slug


def _touch(root: Path, slug: str) -> None:
    project = load(root, slug)
    project["updated"] = _today()
    _write(Path(root) / slug, project)


def _write(folder: Path, project: dict) -> None:
    (folder / "project.json").write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")


def _today() -> str:
    return datetime.date.today().isoformat()
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_projects.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/projects.py tests/test_projects.py
git commit -m "feat(projects): local deck projects with immutable versions and a tuning log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The `project` command

**Files:**
- Modify: `mtgpt/cli.py` (parser, `_project` handler, dispatch, docstring)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything in `mtgpt/projects.py` (Task 7).
- Produces: `project list | new | status | save | best | stage | note | log`, each accepting `--root` (default `projects.DEFAULT_ROOT`):
  - `project list`
  - `project new --name NAME --bracket N (--file F | --stdin) [--source URL]`
  - `project status SLUG`
  - `project save SLUG (--file F | --stdin) [--note TEXT]`
  - `project best SLUG VERSION [--primary FLOAT]`
  - `project stage SLUG STAGE`
  - `project note SLUG TEXT` / `project log SLUG TEXT`
  Each emits `{"ok": true, "command": "project", "data": ...}`; `note`/`log` return `{"slug", "logged": true}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`; add `"project"` to the registered-subcommand set)

```python
LIST_TEXT = "Commander\n1 Hapatra, Vizier of Poisons\n\nDeck\n1 Sol Ring\n98 Swamp\n"


def run_project(capsys, *argv):
    code = cli.main(["project", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_project_lifecycle(tmp_path, capsys):
    deck_file = tmp_path / "list.txt"
    deck_file.write_text(LIST_TEXT)
    root = str(tmp_path / "decks")
    code, out = run_project(capsys, "new", "--root", root, "--name", "Hapatra",
                            "--bracket", "3", "--file", str(deck_file))
    assert code == 0 and out["data"]["slug"] == "hapatra"
    code, out = run_project(capsys, "save", "hapatra", "--root", root,
                            "--file", str(deck_file), "--note", "+X -Y")
    assert out["data"]["version"] == "v2"
    code, out = run_project(capsys, "best", "hapatra", "v2", "--root", root, "--primary", "0.3")
    assert out["data"]["best"] == "v2"
    code, out = run_project(capsys, "stage", "hapatra", "tune", "--root", root)
    assert out["data"]["stage"] == "tune"
    code, out = run_project(capsys, "note", "hapatra", "flooded", "--root", root)
    assert out["data"] == {"slug": "hapatra", "logged": True}
    code, out = run_project(capsys, "list", "--root", root)
    assert [p["slug"] for p in out["data"]] == ["hapatra"]
    code, out = run_project(capsys, "status", "hapatra", "--root", root)
    assert out["data"]["versions"] == ["v1", "v2"]


def test_project_errors_are_envelopes(tmp_path, capsys):
    code, out = run_project(capsys, "status", "nope", "--root", str(tmp_path))
    assert code == 2 and out["ok"] is False
    assert out["error"]["type"] == "ProjectError"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_cli.py -k project`
Expected: FAIL — `invalid choice: 'project'`.

- [ ] **Step 3: Implement**

In `mtgpt/cli.py`, import: `from . import api, projects, tagger` and `from pathlib import Path`.

In `build_parser`, before `return parser`:

```python
    project = sub.add_parser("project", help="Local deck projects (decks/, never committed)")
    psub = project.add_subparsers(dest="project_command", required=True)
    root_opt = argparse.ArgumentParser(add_help=False)
    root_opt.add_argument("--root", default=str(projects.DEFAULT_ROOT),
                          help="Folder holding deck projects (default: the repo's decks/)")
    psub.add_parser("list", parents=[root_opt], help="Every deck project")
    new = psub.add_parser("new", parents=[root_opt], help="Start a project from a decklist")
    new.add_argument("--name", required=True)
    new.add_argument("--bracket", type=int, required=True, choices=[1, 2, 3, 4, 5])
    new.add_argument("--source", help="Where the list came from (a URL)")
    src = new.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    status_cmd = psub.add_parser("status", parents=[root_opt], help="Stage, versions, log tail")
    status_cmd.add_argument("slug")
    save = psub.add_parser("save", parents=[root_opt], help="Write the next version")
    save.add_argument("slug")
    save.add_argument("--note", default="", help="What changed, for the log")
    src = save.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    best = psub.add_parser("best", parents=[root_opt], help="Mark a version as the best so far")
    best.add_argument("slug")
    best.add_argument("version")
    best.add_argument("--primary", type=float, help="Its wins-by-target-round, for `list`")
    stage = psub.add_parser("stage", parents=[root_opt], help="Record the tuning stage")
    stage.add_argument("slug")
    stage.add_argument("stage", choices=projects.STAGES)
    for name, help_text in (("note", "Record how a real game went"),
                            ("log", "Append markdown to the tuning log")):
        cmd = psub.add_parser(name, parents=[root_opt], help=help_text)
        cmd.add_argument("slug")
        cmd.add_argument("text")
```

Add the handler next to `_card_rule`:

```python
def _project(args, command: str) -> int:
    root = Path(args.root)
    action = args.project_command
    if action == "list":
        _emit(command, projects.list_projects(root))
    elif action in ("new", "save"):
        text = _read_deck_text(args, command)
        if text is None:
            return EXIT_USER_ERROR
        if action == "new":
            _emit(command, projects.create(root, args.name, text, bracket=args.bracket,
                                           source=args.source))
        else:
            _emit(command, projects.save(root, args.slug, text, note=args.note))
    elif action == "status":
        _emit(command, projects.status(root, args.slug))
    elif action == "best":
        _emit(command, projects.set_best(root, args.slug, args.version, primary=args.primary))
    elif action == "stage":
        _emit(command, projects.set_stage(root, args.slug, args.stage))
    else:
        (projects.note if action == "note" else projects.log)(root, args.slug, args.text)
        _emit(command, {"slug": args.slug, "logged": True})
    return EXIT_OK
```

In `main`, add before `elif command == "scorecard":`:

```python
        elif command == "project":
            return _project(args, command)
```

(`ProjectError` is an `MtgptError`, so `main`'s existing handler already turns it into an error envelope with exit 2.)

Add to the module docstring's command list:

```
    python3 -m mtgpt.cli project list | new | status | save | best | stage | note | log
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_cli.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/cli.py tests/test_cli.py
git commit -m "feat(cli): project command for listing, starting, and versioning decks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `card-rule-merge` — take in a friend's rules

**Files:**
- Modify: `mtgpt/card_rules.py` (factor out `_save`, add `merge`, `_same`), `mtgpt/api.py` (add `card_rule_merge`), `mtgpt/cli.py` (subcommand + dispatch + docstring)
- Test: `tests/test_card_rules.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `card_rules.load`, `card_rules.validate_rule`.
- Produces: `card_rules.merge(other_path: Path, path: Path | None = None) -> dict` → `{"added": [names], "unchanged": int, "conflicts": [{"name", "mine", "theirs"}]}`. Conflicting entries are never changed. Two entries are the same when `status` and `rule` match (notes and dates are ignored). CLI: `card-rule-merge FILE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_card_rules.py`:

```python
def write_library(path, cards):
    path.write_text(json.dumps({"version": 1, "cards": cards}))
    return path


def test_merge_adds_new_rules_and_reports_conflicts(library, tmp_path):
    card_rules.record("Blood Artist", status="override",
                      rule={"on": "creature_dies", "drain": 1}, note="mine")
    card_rules.record("Sol Ring", status="parsed", note="mine")
    theirs = write_library(tmp_path / "theirs.json", {
        "Blood Artist": {"status": "override", "rule": {"on": "creature_dies", "drain": 2},
                         "note": "theirs", "reviewed": "2026-10-03"},
        "Sol Ring": {"status": "parsed", "note": "different note", "reviewed": "2026-10-03"},
        "Zulaport Cutthroat": {"status": "override",
                               "rule": {"on": "creature_dies", "drain": 1},
                               "note": "theirs", "reviewed": "2026-10-03"},
    })
    result = card_rules.merge(theirs)
    assert result["added"] == ["Zulaport Cutthroat"]
    assert result["unchanged"] == 1
    assert [c["name"] for c in result["conflicts"]] == ["Blood Artist"]
    saved = card_rules.load()["cards"]
    assert saved["Blood Artist"]["rule"]["drain"] == 1  # conflicts are never overwritten
    assert "Zulaport Cutthroat" in saved


def test_merge_rejects_an_invalid_incoming_rule(library, tmp_path):
    theirs = write_library(tmp_path / "theirs.json", {
        "Blood Artist": {"status": "override", "rule": {"on": "nonsense"}, "note": "",
                         "reviewed": "2026-10-03"}})
    with pytest.raises(GoalError):
        card_rules.merge(theirs)
    assert card_rules.load()["cards"] == {}
```

Append to `tests/test_cli.py` (and add `"card-rule-merge"` to the registered set):

```python
def test_card_rule_merge_passes_the_path(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.api, "card_rule_merge",
                        lambda path: {"added": [path], "unchanged": 0, "conflicts": []})
    assert cli.main(["card-rule-merge", str(tmp_path / "theirs.json")]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["added"] == [str(tmp_path / "theirs.json")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_card_rules.py -k merge tests/test_cli.py -k merge`
Expected: FAIL — no `merge`; CLI `invalid choice`.

- [ ] **Step 3: Implement**

In `mtgpt/card_rules.py`, replace the three save lines at the end of `record` with `_save(path, data)` and add:

```python
def merge(other_path: Path, path: Path | None = None) -> dict:
    """Add another library's rules that this one lacks. A card both libraries
    know but judge differently is listed as a conflict and left untouched."""
    path = Path(path or DEFAULT_PATH)
    mine = load(path)
    theirs = load(Path(other_path))
    added, unchanged, conflicts = [], 0, []
    for name, entry in sorted(theirs["cards"].items()):
        current = mine["cards"].get(name)
        if current is None:
            if entry.get("status") == "override":
                validate_rule(name, entry.get("rule"))
            mine["cards"][name] = entry
            added.append(name)
        elif _same(current, entry):
            unchanged += 1
        else:
            conflicts.append({"name": name, "mine": current, "theirs": entry})
    if added:
        _save(path, mine)
    return {"added": added, "unchanged": unchanged, "conflicts": conflicts}


def _same(a: dict, b: dict) -> bool:
    return a.get("status") == b.get("status") and a.get("rule") == b.get("rule")


def _save(path: Path, data: dict) -> None:
    data["cards"] = dict(sorted(data["cards"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
```

(`validate_rule` raises before anything is saved, which is what the second test pins.)

In `mtgpt/api.py`, after `card_rule_set`:

```python
def card_rule_merge(path: str) -> dict:
    """Bring in a friend's card rules; conflicts are listed, never overwritten."""
    from . import card_rules

    return card_rules.merge(path)
```

In `mtgpt/cli.py` `build_parser`, after the `card-rule` parser:

```python
    merge = sub.add_parser("card-rule-merge",
                           help="Add another card_rules.json's rules; list conflicts")
    merge.add_argument("path")
```

In `main`, after `elif command == "card-rule":` branch:

```python
        elif command == "card-rule-merge":
            _emit(command, api.card_rule_merge(args.path))
```

Add to the docstring list: `python3 -m mtgpt.cli card-rule-merge theirs/card_rules.json`.

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_card_rules.py tests/test_cli.py && python3 -m pytest`
Expected: all pass. `test_shipped_library_is_valid` must still pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/card_rules.py mtgpt/api.py mtgpt/cli.py tests/test_card_rules.py tests/test_cli.py
git commit -m "feat(card-rules): merge another library, listing conflicts instead of overwriting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Scryfall rate limits retry with backoff

**Files:**
- Modify: `mtgpt/scryfall.py` (`ScryfallClient._request`, constants near `REQUEST_DELAY` at line 30)
- Test: `tests/test_scryfall.py`

**Interfaces:**
- Produces: `RATE_LIMIT_RETRIES = 3`, `RATE_LIMIT_BACKOFF = 1.0` (seconds, doubled per retry). `_request` retries an HTTP 429 up to three times, sleeping 1, 2, 4 seconds through the client's injected `sleep`, then re-raises the last `HTTPError` (which the callers already turn into `SourceUnavailable`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_scryfall.py`; add `import urllib.error` at the top if missing)

```python
def too_many(url="https://api.scryfall.com/x"):
    return urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)


class FlakyTransport:
    """Raises 429 `fails` times, then answers."""

    def __init__(self, fails, response):
        self.fails, self.response, self.calls = fails, response, 0

    def __call__(self, url, payload=None):
        self.calls += 1
        if self.calls <= self.fails:
            raise too_many(url)
        return self.response


def test_rate_limit_is_retried_with_backoff():
    sleeps = []
    transport = FlakyTransport(2, load("game_changers.json"))
    client = ScryfallClient(transport=transport, sleep=sleeps.append)
    assert client.game_changers()
    assert transport.calls == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limit_gives_up_after_three_retries():
    sleeps = []
    client = ScryfallClient(transport=FlakyTransport(10, {}), sleep=sleeps.append)
    with pytest.raises(SourceUnavailable):
        client.game_changers()
    assert sleeps == [1.0, 2.0, 4.0]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_scryfall.py -k rate_limit`
Expected: FAIL — the first 429 surfaces immediately (`transport.calls == 1`). If `game_changers()` does not wrap `HTTPError` in `SourceUnavailable`, read its body in `mtgpt/scryfall.py` and point the second test at a method that does (`collection`), keeping the assertion on `sleeps`.

- [ ] **Step 3: Implement**

In `mtgpt/scryfall.py`, beside `REQUEST_DELAY`:

```python
#: A 429 (rate limited) is retried this many times, waiting RATE_LIMIT_BACKOFF
#: seconds and doubling each time, before the error is passed on.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF = 1.0
```

Replace the body of `_request` after the courtesy delay:

```python
        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                if payload is not None:
                    return self._transport(url, payload)
                return self._transport(url)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == RATE_LIMIT_RETRIES:
                    raise
                self._sleep(RATE_LIMIT_BACKOFF * 2 ** attempt)
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `python3 -m pytest tests/test_scryfall.py && python3 -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/scryfall.py tests/test_scryfall.py
git commit -m "feat(scryfall): retry rate-limited requests with exponential backoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The skill — move, split, rewrite; packaging

**Files:**
- Move: `skills/mtgpt/` → `.claude/skills/mtgpt/` (with `git mv`)
- Create: `.claude/skills/mtgpt/references/toolkit.md`, `goldfish.md`, `research.md`, `tuning-loop.md`; `scripts/make-zip.sh`
- Rewrite: `.claude/skills/mtgpt/SKILL.md`
- Delete: `.claude-plugin/` (`git rm -r`)
- Modify: `README.md` (Install section), `mtgpt/targets.py:3` (path in docstring)
- Test: `tests/test_skill_docs.py` (new)

**Interfaces:**
- Consumes: the CLI commands from Tasks 6, 8, 9; existing commands.
- Produces: a project skill named `mtgpt` that loads when Claude opens in the repo.

- [ ] **Step 1: Write the failing doc test** — create `tests/test_skill_docs.py`:

```python
"""The skill's docs must only name commands that exist, and its references must exist."""

import re
from pathlib import Path

from mtgpt import cli

SKILL = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "mtgpt"


def test_skill_and_references_exist():
    assert (SKILL / "SKILL.md").exists()
    for name in ("toolkit", "goldfish", "research", "tuning-loop", "brackets",
                 "deckbuilding-hygiene", "sources"):
        assert (SKILL / "references" / f"{name}.md").exists(), name


def test_every_command_the_skill_names_exists():
    actions = [a for a in cli.build_parser()._actions if a.dest == "command"]
    known = set(actions[0].choices)
    text = "\n".join(p.read_text() for p in SKILL.rglob("*.md"))
    named = set(re.findall(r"mtgpt\.cli ([a-z][a-z-]+)", text))
    assert named, "the skill should show commands as `python3 -m mtgpt.cli <command>`"
    assert named <= known, f"unknown commands in the skill: {sorted(named - known)}"


def test_skill_md_stays_short():
    assert len((SKILL / "SKILL.md").read_text().splitlines()) <= 200
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_skill_docs.py`
Expected: FAIL — `.claude/skills/mtgpt/SKILL.md` does not exist.

- [ ] **Step 3: Move and split the existing skill**

```bash
mkdir -p .claude/skills
git mv skills/mtgpt .claude/skills/mtgpt
rmdir skills 2>/dev/null || true
git rm -r -q .claude-plugin
```

Split the old `SKILL.md` without losing text:
- `references/goldfish.md`: the heading `# Goldfishing a deck` followed by everything from the old `### Goldfish a deck` heading up to (not including) `### Investigate a combo`, verbatim. Then add this paragraph at its end:

  ```markdown
  ## Target round

  A goal may set `"target_round"` (a whole number of rounds). The scorecard's
  primary metric is wins by that round; without it the bracket default applies:
  bracket 1–2 → 7, bracket 3 → 5, bracket 4 → 4, bracket 5 → 3.
  ```

- `references/toolkit.md`: the heading `# The toolkit: every command and how to read it` followed by every other section of the old `SKILL.md` after its frontmatter (from `## The rule that matters` to the end, minus the goldfish section above), verbatim. Update its "Reference material" list to name the references by their new paths (`references/…` relative to `.claude/skills/mtgpt/`), and add `scorecard`, `project`, and `card-rule-merge` to the command list in its intro paragraph (the operation count becomes twenty-six).

- [ ] **Step 4: Write `references/research.md`**

```markdown
# Stage 2 — research how this commander is built

Every candidate comes from the toolkit, never from memory, and is verified on
Scryfall before it goes in `research.md`.

## Sources, in order

1. **EDHREC**
   - `python3 -m mtgpt.cli synergy "<commander>" --limit 60` — high-synergy cards and inclusion rates.
   - `python3 -m mtgpt.cli themes "<commander>"` — how the commander is usually built.
   - `python3 -m mtgpt.cli compare --file decks/<slug>/<best>.txt` — what the average deck
     plays that this list does not, tagged by function.
2. **Commander Spellbook — the commander only.**
   `python3 -m mtgpt.cli card-combos "<commander>" > decks/<slug>/combos.json`.
   This file feeds the floor check. Never submit a deck to Spellbook, and do not
   run `combos --file` here.
3. **Function packages** — `python3 -m mtgpt.cli find <function> --identity <colors> --limit 15`
   for each job the deck needs (`ramp`, `protection`, `sacrifice_outlet`, `landfall`, …;
   `find --help` lists them).
4. **Primers and tuned lists** — web search for "<commander> primer" and recent tuned
   lists, from the sources in `references/sources.md`. Sites that block scripts are read
   through the Chrome MCP (load the `claude-in-chrome` skill first).

## The deck's jobs

Before listing candidates, write at the top of `research.md` the jobs this deck
needs, from the goal, the commander's text, and what the sources show. For a
landfall commander: ramp, land recursion, landfall triggers, protection, removal.
For Hapatra: -1/-1 counter placers, token-on-counter payoffs, drains, sac
outlets, protection, removal.

## The multi-job flag

A candidate is **multi-job** when it covers two or more of *this deck's* jobs. It
is a judgment against the jobs list, informed by `classify` tags — not a test for
"modal" or "MDFC". A creature that replays lands from the graveyard and has a
landfall trigger is multi-job in a landfall deck and single-job in a
spellslinger deck. Multi-job candidates are listed first in every group and are
preferred whenever the loop picks a card to add.

## Verify every candidate

- `python3 -m mtgpt.cli classify "<name>" ...` — real card, its functions.
- In the commander's color identity.
- Bracket: no mass land denial below bracket 4; count Game Changers against the
  bracket's allowance (`references/brackets.md`).

## Pre-scan

Run stage 1 on the candidates now (`goldfish-scan` a scratch list of them with the
deck's goal, then `card-rule` each `needs_review` card), so the tuning loop
never stops on an unmodeled card.

## research.md format

    # <name> — research
    ## Deck jobs
    - ramp; -1/-1 placers; token payoffs; drains; sac outlets; protection; removal
    ## Candidates
    ### Protection
    - **Card Name** — multi-job (protection, sac outlet) — EDHREC 41% — sources: EDHREC, primer X — cut: Weak Card

Every candidate line names its sources and a suggested cut.
```

- [ ] **Step 5: Write `references/tuning-loop.md`**

```markdown
# Stage 3 — the tuning loop

## The scorecard

    python3 -m mtgpt.cli scorecard --file decks/<slug>/<best>.txt --goal decks/<slug>/goal.json --bracket <N>

reports wins by the target round (primary), win rate, commander on curve,
interaction held while live (`covered`), opponent win attempts answered and
losses to them, protection, mulligan rate and its causes, untapped-land share,
short colors, `weaknesses` (worst first), and `card_impact` per card.

| Target | Level |
|---|---|
| Commander on curve | ≥ 70% |
| Interaction while live (`covered`) | ≥ 50% |
| Mulligan rate | ≤ 25% |
| Untapped lands | ≥ 80%, no short color |

## Judging a swap

    python3 -m mtgpt.cli scorecard --file decks/<slug>/<best>.txt --file decks/<slug>/<new>.txt \
        --goal decks/<slug>/goal.json --bracket <N> --combos decks/<slug>/combos.json

- **rejected** — the floors failed (a category below its audit minimum, or a new bracket
  error). Nothing was simmed. Pick a different cut.
- **keep** — primary up ≥ 1.5 points, no guard broken. `project best <slug> <new> --primary <value>`.
- **revert** — primary down, or a guard broken (on-curve −3, covered −3, opponent-win
  losses +2, mulligan +3 points, or a color newly short).
- **mixed** — anything else. Decide, and write why in the log: prefer multi-job cards and
  the weakest target.

Close calls (primary moved < 3 points) are re-run at 3000 games automatically;
`games` in the output says which count the verdict used. Bracket warnings (a
two-card combo at bracket 3) come back under `floors.warnings`: mention them.

## Choosing the cut

From the best version's `card_impact`: the lowest `win_delta` / highest
`dead_rate` card that is single-job, `measurable: true`, not a combo piece, and
not a land a short color needs. A card with `measurable: false` is never cut on
its numbers — its value (removal, counters) is invisible to a goldfish.

## The loop

1. Baseline: scorecard the best version; name the top weakness.
2. Pick a swap: in — a `research.md` candidate for that weakness, multi-job first;
   out — chosen as above.
3. Write the new list, `project save <slug> --file new.txt --note "+In -Out (why)"`.
4. Judge it against the best version; act on the verdict.
5. Log it: `project log <slug> "v12 vs v8: keep +2.1 (on-curve 68→72) — +In -Out"`.
6. One swap at a time; pairs only when the cards need each other.
7. Once per checkpoint round, test ±1 land the same way.
8. **Checkpoint** after 10 swaps tried or 3 non-keeps in a row: show the best
   version's scorecard against the round's starting one, the kept swaps and why,
   sim gaps hit; ask continue, change direction, or stop.

## Finishing

1. Land pass: swap tapped lands for untapped ones of the same colors (and fix
   short colors), each judged by the scorecard.
2. Pilot spot-check: pilot games 0–2 of seed 1 (`goldfish-new --seed 1 --game N`,
   `references/goldfish.md`) and report where your line beat or lost to the
   heuristic on the same deal. A big gap is a likely heuristic blind spot: log it.
3. Final report: best version against `v1`, every kept swap and why, sim gaps,
   and the list in paste-ready form for Moxfield/Archidekt import.
4. `project stage <slug> finish`.

## Noise floor

(Filled in by the implementation's calibration run — Task 12.)
```

- [ ] **Step 6: Write the new `SKILL.md`**

```markdown
---
name: mtgpt
description: Use when the user wants to build, import, tune, or continue a Magic the Gathering Commander/EDH deck in this repo - lists saved deck projects, starts new ones (import a link, paste a list, or build from scratch), then always runs scan, research, and tune with the goldfish simulator and a fixed scorecard. Triggers on "EDH", "Commander deck", "my decks", "new deck", "tune my deck", "goldfish", "decklist", "moxfield", "archidekt", "what should I cut".
---

# mtgpt — Commander deck projects

Run commands from the repo root as `python3 -m mtgpt.cli <command>`. Output is
JSON: `{"ok": true, "command": ..., "data": ...}` or `{"ok": false, ..., "error": ...}`.

## First run

`python3 --version` must be 3.12 or newer. On WSL Ubuntu 22.04 (Python 3.10):

    sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.12

then use `python3.12` wherever this skill says `python3`. Do not continue on an older Python.

## Rules that never bend

1. **Decks are save files.** Everything about a deck lives in `decks/<slug>/`, which git
   ignores. Never `git add` anything under `decks/`. The only deck work that is committed
   is card rules (`mtgpt/data/card_rules.json`).
2. **Every card comes from the toolkit** (Scryfall-verified), never from memory.
3. **Never infer the win state or the commander's "thing".** Ask.
4. **Scan before every goldfish.** No sim over unreviewed cards.
5. **Commander Spellbook: look up the commander only** (`card-combos "<commander>"`).
   Never submit a deck to any site.
6. **Goldfish numbers compare versions; they do not predict real games.** Say so in every report.

## Start of every session

1. `python3 -m mtgpt.cli project list` — show name, commander, bracket, best version, its
   wins-by-target-round, last updated. Ask: continue a deck, or start a new one?
2. **Continue:** `python3 -m mtgpt.cli project status <slug>`. Read `playtest_notes` first and
   let them steer this session (adjust the goal's disruption or `opponent_win`; aim at what
   went wrong). Resume at the project's `stage`.
3. **New:** ask the deck's name, the target bracket, and where the list comes from:
   - **A link.** Archidekt: `python3 -m mtgpt.cli import "<url>"` and write its `decklist`
     to a scratch file. Moxfield or another site: load the `claude-in-chrome` skill, open the
     deck in a tab, and read the list. For Moxfield, from a moxfield.com tab fetch
     `https://api2.moxfield.com/v3/decks/all/<id>` with JavaScript and build the list from
     its commanders and mainboard. If Chrome is not connected or the read fails, ask the user
     to paste the list (Moxfield: More → Export → Copy for Moxfield).
   - **Paste.**
   - **From scratch.** Ask the commander, bracket, and the theme or playstyle (no budget —
     decks are proxied). Do stage 2's research first, draft a 99 with multi-job cards first,
     `validate` and `audit` until legal, and show the user the draft for approval.

   Then `python3 -m mtgpt.cli project new --name "<name>" --bracket <N> --file <list> [--source <url>]`.
4. **Gate:** `python3 -m mtgpt.cli validate --file decks/<slug>/v1.txt` and
   `python3 -m mtgpt.cli bracket --file decks/<slug>/v1.txt --target <N>` must pass. If not,
   propose fixes, and with the user's OK `project save` the fixed list.
5. **Goal:** ask the win state and the commander's thing; write `decks/<slug>/goal.json`
   (`references/goldfish.md`). Add `"target_round"` only if the user wants other than the
   bracket default (1–2 → 7, 3 → 5, 4 → 4, 5 → 3). Show it; confirm.
   `python3 -m mtgpt.cli project stage <slug> scan`.

## Stage 1 — scan

    python3 -m mtgpt.cli goldfish-scan --file decks/<slug>/<best>.txt --goal decks/<slug>/goal.json

Record a verdict for every `needs_review` card with `python3 -m mtgpt.cli card-rule`
(`parsed` / `override` / `ignored` — `references/goldfish.md`, step 0). Space the calls out:
Scryfall rate-limits bursts. A card that matters but the engine cannot express is a
**sim gap**: `project log` it, explain what the card does and what the sim would need, and
ask whether to build the engine feature now (test-first) or continue with the gap noted.
Then `project stage <slug> research`.

## Stage 2 — research

Follow `references/research.md`: EDHREC (`synergy`, `themes`, `compare`), the commander's
Spellbook combos saved to `decks/<slug>/combos.json`, `find` packages, and primers. Write the
deck's jobs and verified candidates to `decks/<slug>/research.md`, multi-job first, and
pre-scan the candidates. Then `project stage <slug> tune`.

## Stage 3 — tune

Follow `references/tuning-loop.md`: baseline `scorecard`, then one swap at a time — save,
judge against the best version, act on the verdict, log it — with a checkpoint after 10
swaps or 3 non-keeps in a row. The user chooses continue, change direction, or stop.

## Finishing

Land pass, pilot spot-check, final report with a paste-ready list
(`references/tuning-loop.md`, Finishing). Then `project stage <slug> finish`.

## Real games

When the user says how the deck played: `python3 -m mtgpt.cli project note <slug> "<what happened>"`.

## Sharing

- A friend's new card rules: `python3 -m mtgpt.cli card-rule-merge <their card_rules.json>`;
  show the user each conflict and let them choose (`card-rule` to set the winner).
- A zip for a friend: `scripts/make-zip.sh` (committed files only — no decks).

## References

- `references/tuning-loop.md` — scorecard, verdicts, the loop, finishing
- `references/research.md` — sources, deck jobs, the multi-job judgment
- `references/goldfish.md` — goal file format, engine overrides, pilot mode
- `references/toolkit.md` — every command, auditing, brackets, combos, reading the numbers
- `references/brackets.md`, `references/deckbuilding-hygiene.md`, `references/sources.md`
```

- [ ] **Step 7: Packaging**

Create `scripts/make-zip.sh` and make it executable (`chmod +x scripts/make-zip.sh`):

```bash
#!/usr/bin/env bash
# Build mtgpt.zip from committed files only: local decks and notes never ship.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git archive --format=zip --prefix=mtgpt/ -o mtgpt.zip HEAD
echo "wrote $(pwd)/mtgpt.zip"
```

Add `mtgpt.zip` to `.gitignore`.

In `README.md`, replace the `## Install` section with:

```markdown
## Install

Unzip the repo (or clone it), open a terminal in its folder, and run `claude`.
The `mtgpt` skill loads automatically: ask it to list your decks, start a new
one, or tune one. Requires Python 3.12+ (the skill checks on first run).

Decks live in `decks/`, which git ignores — they are your save files. To share
the tool, run `scripts/make-zip.sh`; the zip holds only committed files.
```

In `mtgpt/targets.py` line 3, change the path to `.claude/skills/mtgpt/references/deckbuilding-hygiene.md`.

- [ ] **Step 8: Run the doc tests and the full suite**

Run: `python3 -m pytest tests/test_skill_docs.py && python3 -m pytest`
Expected: all pass. If `test_every_command_the_skill_names_exists` lists an unknown command, fix the doc text, not the test. Also run `scripts/make-zip.sh && unzip -l mtgpt.zip | grep -c decks/` and expect `0`; then `rm mtgpt.zip`.

- [ ] **Step 9: Commit**

```bash
git add -A .claude/skills scripts/make-zip.sh README.md mtgpt/targets.py .gitignore tests/test_skill_docs.py
git status --short   # confirm skills/ and .claude-plugin/ show as deleted/renamed, nothing under decks/
git commit -m "feat(skill): project skill with deck projects and the scan/research/tune workflow

Moves the skill to .claude/skills/mtgpt so it loads when Claude opens in the
repo, splits detail into on-demand references, drops the plugin manifest
(the repo zip is the distribution), and adds scripts/make-zip.sh.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Migrate the author's decks; calibrate the noise floor

**Files:**
- Create (scratch, never committed): `<scratchpad>/migrate_decks.py`
- Modify: `.claude/skills/mtgpt/references/tuning-loop.md` (Noise floor section); possibly `mtgpt/scorecard.py` constants
- Nothing under `decks/` is committed.

**Interfaces:**
- Consumes: `projects.create`, `projects.set_best`, `projects.set_stage`, the `scorecard` command.

- [ ] **Step 1: Write and run the migration script** (in the session scratchpad, run from the repo root)

```python
# migrate_decks.py — one-off: flat decks/*.txt into project folders. Not committed.
import shutil
from pathlib import Path

from mtgpt import projects

ROOT = Path("decks")
PLAN = [
    # (name, bracket, source, {version: file stem}, best, goal stem for goal.json)
    ("Hapatra", 3, "https://moxfield.com/decks/4L6v6pWx0Ee7N5KnGD9Rpg",
     {"v1": "hapatra-bracket3", **{f"v{n}": f"hapatra-bracket3-v{n}"
                                    for n in (2, 3, 5, 7, 8, 9, 10, 11)},
      "v4a": "hapatra-bracket3-v4a", "v4b": "hapatra-bracket3-v4b",
      "v6a": "hapatra-bracket3-v6a", "v6b": "hapatra-bracket3-v6b",
      "v11b": "hapatra-bracket3-v11b"},
     "v8", "hapatra-bracket3-v8"),
    ("Jodah Big Spells B3", 3, None,
     {"v1": "jodah-big-spells-bracket3", "v5": "jodah-big-spells-bracket3-v5"},
     "v5", "jodah-big-spells-bracket3-v5"),
    ("Jodah Big Spells B4", 4, None, {"v1": "jodah-big-spells-bracket4"},
     "v1", "jodah-big-spells-bracket4"),
    ("Jodah Archmage Eternal B4", 4, None, {"v1": "jodah-archmage-eternal-bracket4"},
     "v1", None),
]

for name, bracket, source, files, best, goal in PLAN:
    v1 = (ROOT / f"{files['v1']}.txt").read_text()
    p = projects.create(ROOT, name, v1, bracket=bracket, source=source)
    folder = ROOT / p["slug"]
    for version, stem in files.items():
        shutil.copy(ROOT / f"{stem}.txt", folder / f"{version}.txt")
        g = ROOT / f"{stem}.goal.json"
        if g.exists():
            shutil.copy(g, folder / f"{version}.goal.json")
    if goal:
        shutil.copy(ROOT / f"{goal}.goal.json", folder / "goal.json")
    projects.set_best(ROOT, p["slug"], best)
    projects.set_stage(ROOT, p["slug"], "tune" if goal else "scan")
    print(p["slug"], projects.versions(ROOT, p["slug"]))
```

Run: `python3 <scratchpad>/migrate_decks.py`
Expected output: four lines — `hapatra [v1, v2, v3, v4a, v4b, v5, v6a, v6b, v7, v8, v9, v10, v11, v11b]`, `jodah-big-spells-b3 [v1, v5]`, `jodah-big-spells-b4 [v1]`, `jodah-archmage-eternal-b4 [v1]`.

- [ ] **Step 2: Verify, then remove the flat files**

Run: `python3 -m mtgpt.cli project list` — four projects. For each project, `diff` its version files against the flat originals (e.g. `diff decks/hapatra-bracket3-v8.txt decks/hapatra/v8.txt`) — no output. Only then delete the flat files: `rm decks/*.txt decks/*.goal.json` (leave `decks/README.md` in place for the author to decide). Confirm `git status --short` shows nothing under `decks/`.

- [ ] **Step 3: Calibrate the noise floor**

Pairing check — a basic swapped for an identical basic must give a zero delta.

Copy `decks/hapatra/v8.txt` to `<scratchpad>/v8-snow.txt`, reducing the `Swamp` line's count by one and adding `1 Snow-Covered Swamp` on the line **directly after** it. `prepare` keeps deck order and matched seeds pair cards by position, so the new basic must take the last Swamp's position. Then:

```bash
python3 -m mtgpt.cli scorecard --file decks/hapatra/v8.txt --file <scratchpad>/v8-snow.txt \
    --goal decks/hapatra/goal.json --bracket 3 --games 1000
```

Expected: `verdict.primary_delta == 0.0`. If it is not zero, first print both decks' card order (`prepare(deck, goal).cards` names) to confirm the basic sits at the same index; if it does and the delta is still non-zero, stop and report: matched seeds are not matching, and every verdict is suspect.

Seed spread — the primary metric of v8 alone at seeds 1–5, at 1000 and 3000 games:

```bash
for g in 1000 3000; do for s in 1 2 3 4 5; do
  python3 -m mtgpt.cli scorecard --file decks/hapatra/v8.txt --goal decks/hapatra/goal.json \
      --bracket 3 --games $g --seed $s | python3 -c "import json,sys; print($g, $s, json.load(sys.stdin)['data']['score']['primary'])"
done; done
```

Compute the standard deviation of the five values at each game count. Matched-seed comparisons are less noisy than this unmatched spread, so it is an upper bound.

- [ ] **Step 4: Record and, if needed, adjust**

Replace the Noise floor section of `references/tuning-loop.md` with the measured numbers:

```markdown
## Noise floor

Calibrated <date> on hapatra v8 (bracket 3, target round 5):
- Pairing check (Swamp → Snow-Covered Swamp): primary delta 0.0 — seeds match.
- Primary across seeds 1–5: SD <x> points at 1000 games, <y> at 3000.
Keep margin <KEEP_MARGIN×100> points; close-call band <CLOSE_CALL×100> points.
```

Rule: if `x` (SD at 1000 games, in points) is greater than 1.5, set `KEEP_MARGIN = round(x / 100, 3)` and `CLOSE_CALL = round(2 * x / 100, 3)` in `mtgpt/scorecard.py`; otherwise leave them. If you changed them, update the numbers in `references/tuning-loop.md` ("Judging a swap"), and re-run `python3 -m pytest` (the verdict tests use 0.03/0.02 deltas; adjust their fixture values if the constants moved).

- [ ] **Step 5: Commit (docs and constants only)**

```bash
git add .claude/skills/mtgpt/references/tuning-loop.md mtgpt/scorecard.py tests/test_scorecard.py
git status --short   # nothing under decks/
git commit -m "docs(tuning-loop): record the scorecard noise-floor calibration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: End-to-end run on the Hapatra project

**Files:**
- Possibly modify: `.claude/skills/mtgpt/SKILL.md` and references (wording fixes found by the run)
- Nothing under `decks/` is committed.

- [ ] **Step 1: Walk the skill as a new user would**

Following only `SKILL.md` and its references, in order:
1. `project list` → the four migrated projects appear with best versions.
2. `project status hapatra` → stage `tune`, versions v1…v11b.
3. Stage 1 on the best version: `goldfish-scan --file decks/hapatra/v8.txt --goal decks/hapatra/goal.json` → `needs_review` should be empty or short (the library already holds Hapatra's rules); review any that appear.
4. Stage 2, abbreviated: `card-combos "Hapatra, Vizier of Poisons" > decks/hapatra/combos.json`; confirm the file holds the envelope with `data.combos` (Task 6's `--combos` reads either shape).
5. Stage 3, one iteration: baseline `scorecard` of v8; read `weaknesses` and `card_impact`; pick one swap from the research per `references/tuning-loop.md`; `project save`; judge with `scorecard --file v8 --file v12 --combos ...`; `project log` the verdict.
6. `project note hapatra "e2e test note"` then `project status hapatra` shows it under `playtest_notes`.

- [ ] **Step 2: Fix what the walk exposed**

Any step where the docs were wrong, ambiguous, or named a flag that does not exist: fix the doc (and add a test if it was code). Re-run `python3 -m pytest`.

- [ ] **Step 3: Final checks**

Run: `python3 -m pytest` — all pass.
Run: `git status --short` — nothing under `decks/`; `scripts/make-zip.sh && unzip -l mtgpt.zip | grep -E "decks/|\.goal\.json" | wc -l` → `0`; `rm mtgpt.zip`.

- [ ] **Step 4: Commit**

```bash
git add -A .claude/skills mtgpt tests
git commit -m "fix(skill): wording and flags found by the end-to-end run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Skip the commit if the walk found nothing to change.)
