"""Exceptions that signal a hard stop.

The pipeline never guesses. When an input cannot be trusted, it raises.
"""


class MtgptError(Exception):
    """Base class for every mtgpt failure."""


class UnresolvedCards(MtgptError):
    """One or more card names could not be resolved against Scryfall.

    This is the guard against hallucinated and misspelled cards. It carries the
    offending names so the caller can echo them back verbatim.
    """

    def __init__(self, names):
        self.names = tuple(names)
        joined = ", ".join(self.names)
        super().__init__(
            f"{len(self.names)} card name(s) could not be found on Scryfall: {joined}. "
            "Fix the spelling or remove the entries; mtgpt will not guess."
        )


class DeckStructureError(MtgptError):
    """The decklist text could not be parsed into a deck at all."""


class SourceUnavailable(MtgptError):
    """An upstream data source failed. Callers may degrade rather than stop."""

    def __init__(self, source, detail):
        self.source = source
        self.detail = detail
        super().__init__(f"{source} unavailable: {detail}")
