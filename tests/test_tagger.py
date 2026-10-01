import pytest

from mtgpt import tagger
from mtgpt.models import Function


def test_every_mapped_function_tag_is_in_the_verified_vocabulary():
    # The point of tagger.TAGS is that nothing ships unprobed. A Function
    # mapped to a label that is not in TAGS would send Scryfall a tag nobody
    # confirmed, and come back as a 404 that reads like an empty result.
    for function, label in tagger.FUNCTION_TAGS.items():
        assert label in tagger.TAGS, function


def test_cross_check_only_references_verified_labels():
    for label in tagger.CROSS_CHECK:
        assert label in tagger.TAGS, label


def test_rejected_tags_are_not_also_shipped():
    # REJECTED exists so a plausible name is not re-added on the strength of
    # the name alone. A value in both lists would defeat that.
    shipped = set(tagger.TAGS.values())
    assert shipped.isdisjoint(tagger.REJECTED)


def test_mass_land_denial_has_no_tag_and_that_is_recorded():
    # Every candidate name 404s, so `find` cannot serve this function. The
    # absence is documented rather than papered over with a wrong tag.
    assert Function.MASS_LAND_DENIAL in tagger.UNMAPPED_FUNCTIONS
    assert Function.MASS_LAND_DENIAL not in tagger.FUNCTION_TAGS


def test_unmapped_functions_is_exactly_the_complement():
    assert tagger.UNMAPPED_FUNCTIONS == frozenset(
        set(Function) - set(tagger.FUNCTION_TAGS)
    )


@pytest.mark.parametrize(
    "given,expected",
    [
        ("ramp", "ramp"),
        ("spot_removal", "spot-removal"),
        # The hyphenated otag is accepted as well as the snake_case label.
        ("spot-removal", "spot-removal"),
        ("RAMP", "ramp"),
        # EDHREC-style plural does not exist upstream; ours maps onto it.
        ("extra_turns", "extra-turn"),
        ("wincon", "win-condition"),
    ],
)
def test_function_tag_resolves_labels(given, expected):
    assert tagger.function_tag(given) == expected


def test_function_tag_accepts_a_function_member():
    assert tagger.function_tag(Function.SWEEPER) == "sweeper"


def test_function_tag_rejects_an_unverified_tag_and_lists_the_vocabulary():
    with pytest.raises(ValueError) as exc:
        tagger.function_tag("stax")
    message = str(exc.value)
    assert "stax" in message
    # The message must carry the valid options: the failure mode it guards
    # against is a 404 that looks like an empty result.
    assert "ramp" in message


@pytest.mark.parametrize(
    "given,expected",
    [
        (None, ""),
        ("wubg", "ci:wubg"),
        ("WUBG", "ci:wubg"),
        ("w,u,b,g", "ci:wubg"),
        # Order is normalized so two spellings produce one query string.
        ("gbuw", "ci:wubg"),
        ("{R}", "ci:r"),
        ("", ""),
    ],
)
def test_identity_filter(given, expected):
    assert tagger.identity_filter(given) == expected


def test_identity_filter_rejects_a_non_colour_letter():
    # A typo must fail loudly: silently dropping it searches the whole format.
    with pytest.raises(ValueError):
        tagger.identity_filter("wubz")


def test_build_query_always_scopes_to_commander():
    assert tagger.build_query("ramp", identity="wubg") == (
        "otag:ramp legal:commander ci:wubg"
    )


def test_build_query_without_an_identity():
    assert tagger.build_query("sweeper") == "otag:sweeper legal:commander"


def test_build_query_parenthesizes_extra_terms():
    # Unparenthesized extra terms containing OR would rebind the whole query.
    query = tagger.build_query("ramp", identity="g", extra="cmc<=2 or t:creature")
    assert query == "otag:ramp legal:commander ci:g (cmc<=2 or t:creature)"


def test_cross_check_functions_is_empty_for_a_tag_classify_cannot_judge():
    # `wheel` is searchable but classify.py has no equivalent notion, so
    # claiming agreement either way would be inventing a verdict.
    assert tagger.cross_check_functions("wheel") == frozenset({Function.DRAW})
    assert tagger.cross_check_functions("theft") == frozenset()


def test_cross_check_removal_accepts_either_removal_tag():
    # otag:removal covers both of ours, so agreement is an intersection.
    assert tagger.cross_check_functions("removal") == frozenset(
        {Function.SPOT_REMOVAL, Function.SWEEPER}
    )


def test_canonical_label_normalizes_and_validates():
    assert tagger.canonical_label("spot-removal") == "spot_removal"
    with pytest.raises(ValueError):
        tagger.canonical_label("land-destruction")


def test_vocabulary_is_sorted_and_non_empty():
    vocabulary = tagger.vocabulary()
    assert vocabulary
    assert list(vocabulary) == sorted(vocabulary)
