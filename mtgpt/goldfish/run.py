# mtgpt/goldfish/run.py
"""Play many auto games and turn them into the goldfish report.

Game `i` of a run with seed `s` is seeded with the string f"{s}-{i}", so two
decks simulated with the same seed are dealt from the same shuffles and roll
the same disruption dice. That pairing is what lets `compare` attribute a
difference to the cards rather than to luck.
"""

from __future__ import annotations

from collections import Counter

from ..goal import ENGINE_TAGS, Condition
from ..models import ResolvedDeck
from .engine import DEFAULT_TURN_CAP, GameState, Setup, apply, new_game, prepare
from .policy import choose

DEFAULT_GAMES = 1000
GOLDFISH_NOTE = (
    "Goldfish: no opponents, no blockers, no interaction against you except the "
    "disruption dice. Turns are optimistic by construction; use the numbers to "
    "compare versions of a deck, not to predict real games."
)


def play(setup: Setup, *, seed, turn_cap: int = DEFAULT_TURN_CAP,
         disruption: bool = True) -> GameState:
    """One auto game, played to a win or the turn cap."""
    state = new_game(setup, seed=seed, turn_cap=turn_cap, disruption=disruption)
    while not state.over:
        apply(state, choose(state), in_place=True)
    return state


def simulate(deck: ResolvedDeck, goal_raw: dict, *, games: int = DEFAULT_GAMES,
             turn_cap: int = DEFAULT_TURN_CAP, seed: int = 1,
             disruption: bool = True) -> dict:
    """Play `games` auto games and summarize them."""
    if games < 1:
        raise ValueError("games must be 1 or more")
    setup = prepare(deck, goal_raw)
    states = [play(setup, seed=f"{seed}-{i}", turn_cap=turn_cap, disruption=disruption)
              for i in range(games)]
    return summarize(setup, states, seed=seed, turn_cap=turn_cap, disruption=disruption)


def compare(before: ResolvedDeck, after: ResolvedDeck, goal_raw: dict, **options) -> dict:
    """Simulate two decks on the same seeds; report both and the differences."""
    a = simulate(before, goal_raw, **options)
    b = simulate(after, goal_raw, **options)
    return {"before": a, "after": b, "delta": _delta(a, b)}


def summarize(setup: Setup, states: list[GameState], *, seed, turn_cap: int,
              disruption: bool) -> dict:
    games = len(states)
    goal = setup.goal
    return {
        "games": games,
        "seed": seed,
        "turn_cap": turn_cap,
        "disruption_enabled": disruption and goal.disruption is not None,
        "setup": _setup_block(states, turn_cap),
        "commander": _commander_block(states, goal.commander_turn),
        "thing": _thing_block(states),
        "disruption": _disruption_block(states),
        "win": _win_block(states),
        "loss": _loss_block(states),
        "notes": {
            "unmodeled": sorted({c.name for c in setup.cards if c.unmodeled}
                                - {name for name, _ in goal.engine}),
            "goal_warnings": goal_warnings(setup),
            "goldfish": GOLDFISH_NOTE,
        },
    }


def goal_warnings(setup: Setup) -> list[str]:
    """A `count` in thing or win whose tag no card in the deck can carry: that
    condition can never hold, which reads as a bad deck rather than a bad goal."""
    goal = setup.goal
    out = []
    for field, cond in (("thing", goal.thing), ("win", goal.win)):
        for tag in _count_tags(cond):
            if tag == "creature" or _deck_can_carry(setup, tag):
                continue
            if tag in ENGINE_TAGS:
                fix = "add an engine override"
            elif tag == "equipment_aura":
                fix = "the deck has no Equipment or Aura"
            else:
                fix = "no card is classified that way"
            warning = f"{field} counts '{tag}' but no card in the deck has that tag — {fix}"
            if warning not in out:
                out.append(warning)
    return out


def _count_tags(cond: Condition) -> list[str]:
    tags = [cond.key] if cond.kind == "count" else []
    for child in cond.children:
        tags += _count_tags(child)
    return tags


def _deck_can_carry(setup: Setup, tag: str) -> bool:
    if tag in ENGINE_TAGS:
        return any(spec.has_tag(tag) for _, spec in setup.goal.engine)
    if tag == "equipment_aura":
        return any(t in c.type_line for c in setup.cards for t in ("Equipment", "Aura"))
    return any(tag in c.functions for c in setup.cards)


def _setup_block(states, turn_cap):
    """Report setup phase statistics.

    mana_by_turn and lands_by_turn average over games that survived to each turn
    (see games_by_turn for the survival count by turn).
    pre_commander_mana_spent_on covers only turns strictly before the commander's
    first cast.
    """
    games = len(states)
    mana_by_turn, lands_by_turn, games_by_turn = [], [], []
    for turn in range(1, turn_cap + 1):
        rows = [t for s in states for t in s.per_turn if t["turn"] == turn]
        mana_by_turn.append(_mean([r["mana"] for r in rows]))
        lands_by_turn.append(_mean([r["lands"] for r in rows]))
        games_by_turn.append(len(rows))
    spent = Counter()
    for s in states:
        spent.update(s.pre_commander)
    total = sum(spent.values())
    stalled = sum(1 for s in states if any(
        t["turn"] == 3 and t["lands"] <= 2 and t["mana"] <= t["lands"] for t in s.per_turn))
    return {
        "mana_by_turn": mana_by_turn,
        "lands_by_turn": lands_by_turn,
        "games_by_turn": games_by_turn,
        "pre_commander_mana_spent_on": {
            k: _ratio(spent[k], total) for k in ("ramp", "engine", "other", "unspent")},
        "mulligan_rate": _ratio(sum(1 for s in states if s.mulligans), games),
        "stalled_rate": _ratio(stalled, games),
    }


def _commander_block(states, target_turn):
    games = len(states)
    cast = [s.commander_cast_turn for s in states if s.commander_cast_turn is not None]
    reasons = Counter(s.late_reason for s in states if s.late_reason is not None)
    return {
        "target_turn": target_turn,
        "on_curve_rate": _ratio(sum(1 for t in cast if t <= target_turn), games),
        "cast_rate": _ratio(len(cast), games),
        "cast_turn": _distribution(cast),
        "late_reasons": {k: _ratio(v, games) for k, v in sorted(reasons.items())},
    }


def _thing_block(states):
    games = len(states)
    online = [s.checkpoints["thing"] for s in states if s.checkpoints["thing"] is not None]
    turns = [t for s in states for t in s.thing_turns]
    covered = sum(1 for t in turns if t["protection"] >= 1 and t["removal"] >= 1)
    return {
        "online_rate": _ratio(len(online), games),
        "online_turn": _distribution(online),
        "interaction_while_online": {
            k: _mean([t[k] for t in turns]) for k in ("removal", "protection", "counterspell")},
        "covered_rate": _ratio(covered, len(turns)),
    }


def _disruption_block(states):
    events = [e for s in states for e in s.events]
    stopped = sum(1 for e in events if e["stopped"])
    recovery, never = [], 0
    hit_games = [s for s in states if any(not e["stopped"] for e in s.events)]
    for s in states:
        online = sorted(t["turn"] for t in s.thing_turns)
        for e in s.events:
            if e["stopped"]:
                continue
            later = [t for t in online if t >= e["turn"]]
            if later:
                recovery.append(later[0] - e["turn"])
            else:
                never += 1
    return {
        "events": len(events),
        "landed": len(events) - stopped,
        "stopped_by_protection_rate": _ratio(stopped, len(events)),
        "recovery_turns": {"median": _percentile(recovery, 0.5)},
        "never_recovered": never,
        "win_rate_after_event": _ratio(
            sum(1 for s in hit_games if s.checkpoints["win"] is not None), len(hit_games)),
    }


def _win_block(states):
    games = len(states)
    wins = [s.checkpoints["win"] for s in states if s.checkpoints["win"] is not None]
    by = Counter(s.win_by for s in states if s.win_by is not None)
    return {
        "win_rate": _ratio(len(wins), games),
        "win_turn": _distribution(wins),
        "by_condition": {k: _ratio(v, games) for k, v in sorted(by.items())},
    }


def _loss_block(states):
    """Games lost before the cap: decking is the only way to lose a goldfish."""
    games = len(states)
    lost = [s.checkpoints.get("loss") for s in states if s.checkpoints.get("loss") is not None]
    by = Counter(s.loss_by for s in states if s.loss_by is not None)
    return {
        "loss_rate": _ratio(len(lost), games),
        "loss_turn": _distribution(lost),
        "by_reason": {k: _ratio(v, games) for k, v in sorted(by.items())},
    }


def _distribution(turns: list[int]) -> dict:
    return {
        "p25": _percentile(turns, 0.25),
        "median": _percentile(turns, 0.5),
        "p75": _percentile(turns, 0.75),
        "histogram": {str(t): n for t, n in sorted(Counter(turns).items())},
    }


def _percentile(values, q: float):
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def _ratio(n, total):
    return round(n / total, 4) if total else None


# Sentinel value to distinguish padding from genuine None
_MISSING = object()


def _delta(a, b):
    """Compare two reports, returning each metric's before, after, and difference.

    - For dicts: iterate union of keys, treating missing keys as 0 for numbers.
    - For numbers: after - before (rounded to 4 places).
    - For lists: diff elementwise, padding shorter list with _MISSING (treated as 0).
    - If exactly one side is None and the other a number, delta is None (visible change).
    - Exclude bools and the keys 'seed', 'games', 'turn_cap'.
    - One-sided dicts and lists are diffed against empty dict / list of _MISSING.
    - Lists of anything but numbers and None (card names) are left out.
    """
    out = {}
    for key in set(a) | set(b):
        if key in ("seed", "games", "turn_cap"):
            continue
        before = a.get(key, _MISSING)
        after = b.get(key, _MISSING)

        if isinstance(before, dict) or isinstance(after, dict):
            if before is _MISSING:
                before = {}
            if after is _MISSING:
                after = {}
            if isinstance(before, dict) and isinstance(after, dict):
                nested = _delta(before, after)
                if nested:
                    out[key] = nested
        elif isinstance(before, list) or isinstance(after, list):
            if before is _MISSING:
                before = []
            if after is _MISSING:
                after = []
            if _numeric_list(before) and _numeric_list(after):
                n = max(len(before), len(after))
                before = list(before) + [_MISSING] * (n - len(before))
                after = list(after) + [_MISSING] * (n - len(after))
                out[key] = [_diff_pair(bv, av) for bv, av in zip(before, after)]
        elif before is not _MISSING and after is not _MISSING:
            if _is_numeric(before) and _is_numeric(after):
                out[key] = round(after - before, 4)
            elif before is None and (after is None or _is_numeric(after)) or (
                    after is None and _is_numeric(before)):
                out[key] = None
        elif _is_numeric(before) or _is_numeric(after):
            out[key] = _diff_pair(before, after)

    return out


def _numeric_list(values) -> bool:
    return isinstance(values, list) and all(v is None or _is_numeric(v) for v in values)


def _is_numeric(x):
    """Check if a value is numeric but not boolean."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _diff_pair(before, after):
    """Diff two values elementwise. _MISSING (padding) counts as 0 against a
    number; anything else that is not two numbers (a genuine None) is None."""
    if _is_numeric(before) and _is_numeric(after):
        return round(after - before, 4)
    if before is _MISSING and _is_numeric(after):
        return round(after, 4)
    if after is _MISSING and _is_numeric(before):
        return round(-before, 4)
    return None
