"""Check a resolved deck against Commander's construction rules.

Returns findings rather than raising, so a report can show every problem at
once instead of surfacing them one run at a time.
"""

from __future__ import annotations

from .models import ResolvedDeck, Severity, Violation

#: A Commander deck is 100 cards including the command zone.
DECK_SIZE = 100


def validate(deck: ResolvedDeck) -> tuple[Violation, ...]:
    """Return every rules violation found, errors first."""
    findings: list[Violation] = []
    findings.extend(_check_size(deck))
    findings.extend(_check_commanders(deck))
    findings.extend(_check_singleton(deck))
    findings.extend(_check_color_identity(deck))
    findings.extend(_check_banned(deck))
    return tuple(sorted(findings))


def _check_size(deck: ResolvedDeck) -> list[Violation]:
    total = deck.total_with_commanders
    if total == DECK_SIZE:
        return []
    direction = "too few" if total < DECK_SIZE else "too many"
    delta = abs(DECK_SIZE - total)
    return [
        Violation(
            severity=Severity.ERROR,
            code="deck_size",
            message=(
                f"Deck has {total} cards including the command zone; "
                f"Commander requires {DECK_SIZE} ({direction} by {delta})."
            ),
        )
    ]


def _check_commanders(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    if not deck.commanders:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="commander_missing",
                message=(
                    "No commander found. Mark it with a 'Commander' section header "
                    "or a '*CMDR*' tag in the decklist."
                ),
            )
        )
        return findings

    if len(deck.commanders) > 2:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="commander_count",
                message=(
                    f"{len(deck.commanders)} commanders declared. Only Partner, "
                    "Friends Forever, or Background pairs may exceed one."
                ),
            )
        )

    for commander in deck.commanders:
        if "Legendary" not in commander.type_line and "Background" not in commander.type_line:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="commander_not_legendary",
                    message=(
                        f"{commander.name} is not legendary, so it cannot be a "
                        f"commander (type: {commander.type_line})."
                    ),
                )
            )
    return findings


def _check_singleton(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    for qty, card in deck.cards:
        if qty > 1 and not card.is_basic_land:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="singleton",
                    message=(
                        f"{card.name} appears {qty} times. Commander is singleton; "
                        "only basic lands may repeat."
                    ),
                )
            )
    return findings


def _check_color_identity(deck: ResolvedDeck) -> list[Violation]:
    if not deck.commanders:
        return []
    allowed = deck.command_zone_identity
    findings: list[Violation] = []
    for _, card in deck.cards:
        outside = card.color_identity - allowed
        if outside:
            symbols = "".join(sorted(outside))
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="color_identity",
                    message=(
                        f"{card.name} has color identity outside the commander's: "
                        f"{{{symbols}}} not in {{{''.join(sorted(allowed)) or 'C'}}}."
                    ),
                )
            )
    return findings


def _check_banned(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    for card in list(deck.commanders) + [c for _, c in deck.cards]:
        if card.is_banned:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="banned",
                    message=f"{card.name} is banned in Commander.",
                )
            )
        elif not card.is_legal:
            findings.append(
                Violation(
                    severity=Severity.WARNING,
                    code="banned",
                    message=(
                        f"{card.name} is not legal in Commander "
                        f"(status: {card.legal_commander})."
                    ),
                )
            )
    return findings
