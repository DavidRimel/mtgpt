"""Check a deck against the WotC Commander bracket it is aiming at.

Layer 1 decides the four constraints that card data alone can settle: Game
Changer count, mass land denial, extra-turn density, and tutor density.

Two-card infinite combo detection needs Commander Spellbook data, supplied via
the optional `combos` argument to `check`. When it is not supplied, the report
states that gap in `deferred_checks`, because a bracket verdict that silently
skips a rule is worse than no verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .classify import classify_deck
from .models import Card, Function, ResolvedDeck, Severity, Violation

F = Function

#: Extra-turn spells tolerated below bracket 4 before the density is flagged.
EXTRA_TURN_WARN_AT = 3

#: Tutors tolerated at brackets 1-2, where the guidance asks for sparse tutoring.
TUTOR_WARN_AT = 4


@dataclass(frozen=True)
class BracketRule:
    """One bracket's constraints. `None` means unrestricted."""

    number: int
    name: str
    game_changers_max: int | None
    allow_mass_land_denial: bool
    watch_extra_turns: bool
    tutor_guidance: str
    allow_two_card_combos: bool


RULES: dict[int, BracketRule] = {
    1: BracketRule(1, "Exhibition", 0, False, True, "minimal", False),
    2: BracketRule(2, "Core", 0, False, True, "sparse", False),
    3: BracketRule(3, "Upgraded", 3, False, True, "unrestricted", False),
    4: BracketRule(4, "Optimized", None, True, False, "unrestricted", True),
    5: BracketRule(5, "cEDH", None, True, False, "unrestricted", True),
}

#: Checks Layer 1 cannot perform without combo data.
COMBO_DEFERRED = (
    "Two-card infinite combo detection requires Commander Spellbook (not supplied)."
)
EXTRA_TURN_APPROXIMATION = (
    "Chained extra turns are approximated by counting extra-turn spells, not by "
    "detecting repeatability."
)


@dataclass(frozen=True)
class BracketReport:
    """Verdict for one target bracket."""

    target: int
    target_name: str
    findings: tuple[Violation, ...]
    game_changers: tuple[str, ...]
    tutor_count: int
    mass_land_denial: tuple[str, ...]
    extra_turns: tuple[str, ...]
    deferred_checks: tuple[str, ...]

    @property
    def compliant(self) -> bool:
        """True when nothing rises to an error. Warnings do not block."""
        return not any(f.severity is Severity.ERROR for f in self.findings)


def check(
    deck: ResolvedDeck,
    tags: dict[str, frozenset[Function]] | None = None,
    target: int = 3,
    combos: tuple[dict, ...] | None = None,
) -> BracketReport:
    """Compare the deck against `target` bracket's constraints."""
    if target not in RULES:
        raise ValueError(f"Bracket must be 1-5, got {target}.")
    rule = RULES[target]
    tags = tags if tags is not None else classify_deck(deck)

    # Every scan considers the command zone, not just the 99: a commander that is
    # itself a tutor or a land wipe must count the same as one in the deck.
    scanned: tuple[tuple[int, Card], ...] = tuple(deck.cards) + tuple(
        (1, commander) for commander in deck.commanders
    )

    if tags is not None:
        scanned_names = {card.name for _, card in scanned}
        missing = scanned_names - set(tags)
        if missing:
            raise ValueError(
                f"tags is missing {len(missing)} card(s) present in the deck: "
                f"{', '.join(sorted(missing)[:5])}. Pass classify_deck(deck) or omit tags."
            )

    game_changers = tuple(card.name for _, card in scanned if card.is_game_changer)

    mld = tuple(
        card.name
        for _, card in scanned
        if F.MASS_LAND_DENIAL in tags.get(card.name, frozenset())
    )
    extra_turns = tuple(
        card.name
        for _, card in scanned
        if F.EXTRA_TURNS in tags.get(card.name, frozenset())
    )
    tutor_count = sum(
        qty for qty, card in scanned if F.TUTOR in tags.get(card.name, frozenset())
    )

    findings: list[Violation] = []

    if rule.game_changers_max is not None and len(game_changers) > rule.game_changers_max:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="game_changers",
                message=(
                    f"{len(game_changers)} Game Changers, but bracket {rule.number} "
                    f"({rule.name}) allows at most {rule.game_changers_max}: "
                    f"{', '.join(game_changers)}."
                ),
            )
        )

    if mld and not rule.allow_mass_land_denial:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="mass_land_denial",
                message=(
                    f"Bracket {rule.number} ({rule.name}) excludes mass land denial: "
                    f"{', '.join(mld)}."
                ),
            )
        )

    if rule.watch_extra_turns and len(extra_turns) >= EXTRA_TURN_WARN_AT:
        findings.append(
            Violation(
                severity=Severity.WARNING,
                code="extra_turns",
                message=(
                    f"{len(extra_turns)} extra-turn spells. Bracket {rule.number} "
                    f"({rule.name}) asks that extra turns not be chained: "
                    f"{', '.join(extra_turns)}."
                ),
            )
        )

    if rule.tutor_guidance in {"minimal", "sparse"} and tutor_count >= TUTOR_WARN_AT:
        findings.append(
            Violation(
                severity=Severity.WARNING,
                code="tutor_density",
                message=(
                    f"{tutor_count} tutors, while bracket {rule.number} ({rule.name}) "
                    f"expects {rule.tutor_guidance} tutoring. Land fetches are not "
                    "counted here."
                ),
            )
        )

    two_card = tuple(c for c in (combos or ()) if c.get("card_count") == 2)
    if combos is not None and two_card and not rule.allow_two_card_combos:
        names = "; ".join(" + ".join(c["cards"]) for c in two_card[:3])
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="two_card_combo",
                message=(
                    f"{len(two_card)} two-card infinite combo(s) detected, which bracket "
                    f"{rule.number} ({rule.name}) excludes: {names}."
                ),
            )
        )

    deferred = (
        (EXTRA_TURN_APPROXIMATION,)
        if combos is not None
        else (COMBO_DEFERRED, EXTRA_TURN_APPROXIMATION)
    )

    return BracketReport(
        target=rule.number,
        target_name=rule.name,
        findings=tuple(sorted(findings)),
        game_changers=game_changers,
        tutor_count=tutor_count,
        mass_land_denial=mld,
        extra_turns=extra_turns,
        deferred_checks=deferred,
    )
