"""Check a resolved deck against Commander's construction rules.

Returns findings rather than raising, so a report can show every problem at
once instead of surfacing them one run at a time.
"""

from __future__ import annotations

from .models import ResolvedDeck, Severity, Violation

#: A Commander deck is 100 cards including the command zone.
DECK_SIZE = 100


def _can_be_commander(card) -> bool:
    """Legendary creature, Background, or a card that says it can be a commander.

    The oracle-text clause is what admits planeswalkers like Daretti, Scrap
    Savant while still rejecting Jace, the Mind Sculptor and The One Ring.
    """
    type_line = card.front_type_line
    if "Legendary" in type_line and "Creature" in type_line:
        return True
    if "Background" in type_line:
        return True
    return "can be your commander" in (card.oracle_text or "").lower()


def validate(deck: ResolvedDeck) -> tuple[Violation, ...]:
    """Return every rules violation found, errors first."""
    findings: list[Violation] = []
    findings.extend(_check_size(deck))
    findings.extend(_check_commanders(deck))
    findings.extend(_check_command_zone_duplicates(deck))
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
        if not _can_be_commander(commander):
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


def _check_command_zone_duplicates(deck: ResolvedDeck) -> list[Violation]:
    """A commander may not also appear in the 99.

    Singleton is enforced per-entry by _check_singleton, which only sees
    deck.cards. Without this cross-zone check, two copies of one card — one in
    the command zone, one in the deck — report as a legal 100-card deck.
    """
    if not deck.commanders:
        return []
    commander_names = {c.name.casefold() for c in deck.commanders}
    findings: list[Violation] = []
    for _, card in deck.cards:
        if card.name.casefold() in commander_names:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="duplicate_in_command_zone",
                    message=(
                        f"{card.name} is the commander and also appears in the 99. "
                        "A card may be in one zone or the other, not both."
                    ),
                )
            )
    return findings


def _check_singleton(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    for qty, card in deck.cards:
        if qty > 1 and not card.is_basic_land and not card.allows_any_number:
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
