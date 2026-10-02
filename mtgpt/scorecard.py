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
