# mtgpt/scorecard.py
"""Reduce a goldfish run to the tuning targets, and judge one version against another.

The tuning loop asks one question after every swap: is the new version better?
Answering it by eye drifts from session to session, so the answer lives here as
fixed numbers: target levels for naming the weakest area, and guard tolerances
for the keep / revert / mixed verdict. Change the numbers here and only here.
"""

from __future__ import annotations

from . import card_rules
from .audit import AuditReport, audit
from .brackets import check
from .classify import classify_deck
from .goal import GoalError
from .goldfish.engine import DEFAULT_TURN_CAP, Setup, prepare
from .goldfish.run import DEFAULT_GAMES, simulate
from .models import ResolvedDeck, Severity
from .validate import validate

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

#: Validation errors whose message holds a count, so only the code identifies them.
COUNTED_CODES = frozenset({"deck_size", "commander_count"})

#: Functions whose value a goldfish cannot see beyond answering opponent wins.
NOT_MEASURABLE_FUNCTIONS = frozenset({"spot_removal", "sweeper", "counterspell"})


def target_round(goal_raw: dict, bracket: int) -> int:
    """The goal's target round (a whole number, 1 or more), else the bracket's default."""
    value = goal_raw.get("target_round")
    if value is None:
        return DEFAULT_TARGET_ROUND[bracket]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GoalError("target_round", "must be a whole number of rounds, 1 or more", [value])
    return value


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
        was = counts_before.get(cat.function, 0)
        if cat.count < cat.target_min and cat.count < was:
            rejected.append(f"{cat.function.value}: {cat.count} is below the minimum "
                            f"{cat.target_min} (was {was})")
    assembled_before, assembled_after = _assembled(combos, before), _assembled(combos, after)
    old = check(before, tags_before, target=bracket, combos=assembled_before)
    new = check(after, tags_after, target=bracket, combos=assembled_after)
    old_codes = {f.code for f in old.findings}
    for finding in new.findings:
        if finding.code in old_codes and (
                _magnitude(finding.code, new, assembled_after)
                <= _magnitude(finding.code, old, assembled_before)):
            continue
        (rejected if finding.severity is Severity.ERROR else warnings).append(finding.message)
    return {"ok": not rejected, "rejected": rejected, "warnings": warnings}


def legality(before: ResolvedDeck, after: ResolvedDeck) -> list[str]:
    """Commander-rule errors `after` has that `before` did not.

    Errors are matched by code and message, except the size and commander-count
    errors, whose message carries a number: those count as new only when
    `before` had no error of that code.
    """
    def key(v):
        return (v.code, "" if v.code in COUNTED_CODES else v.message)

    known = {key(v) for v in validate(before) if v.severity is Severity.ERROR}
    return [v.message for v in validate(after)
            if v.severity is Severity.ERROR and key(v) not in known]


def _assembled(combos, deck: ResolvedDeck) -> tuple[dict, ...]:
    """The cached combos whose every piece is in `deck`, commander included."""
    present = ({c.name.casefold() for c in deck.commanders}
               | {c.name.casefold() for _, c in deck.cards})
    return tuple(c for c in combos
                 if c.get("cards") and all(n.casefold() in present for n in c["cards"]))


def _magnitude(code: str, report, assembled: tuple[dict, ...]) -> int:
    """How big a bracket finding is, so 'worse than before' is a number."""
    if code == "game_changers":
        return len(report.game_changers)
    if code == "mass_land_denial":
        return len(report.mass_land_denial)
    if code == "extra_turns":
        return len(report.extra_turns)
    if code == "tutor_density":
        return report.tutor_count
    if code == "two_card_combo":
        return sum(1 for c in assembled if c.get("card_count") == 2)
    return 0


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
    illegal = legality(before_deck, after_deck)
    if illegal:
        gate = {"ok": False, "rejected": illegal + gate["rejected"], "warnings": gate["warnings"]}
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
