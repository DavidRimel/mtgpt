import pytest

from mtgpt import targets
from mtgpt.audit import audit
from mtgpt.models import Card, Function, ResolvedDeck

F = Function


def card(name, type_line="Creature — Bear", oracle_text="", mv=2.0, cost="{1}{G}",
         produced=(), identity="G"):
    return Card(
        name=name,
        mana_value=mv,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost=cost,
        color_identity=frozenset(identity),
        colors=frozenset(identity),
        legal_commander="legal",
        produced_mana=frozenset(produced),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )


FOREST = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)", mv=0.0, cost="",
              produced="G")
SOL_RING = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", mv=1.0, cost="{1}",
                produced="C", identity="")


def build(cards, commanders=()):
    return ResolvedDeck(commanders=commanders, cards=tuple(cards))


def test_counts_lands_separately_from_mdfc_lands():
    agadeem = card(
        "Agadeem's Awakening // Agadeem, the Undercrypt",
        "Sorcery // Land", "Return from your graveyard...", mv=6.0,
        cost="{X}{B}{B}{B}", produced="B", identity="B",
    )
    report = audit(build([(30, FOREST), (1, agadeem)]))
    assert report.land_count == 30
    assert report.mdfc_land_count == 1


def test_average_mana_value_excludes_lands():
    deck = build([(36, FOREST), (1, card("Two Drop", mv=2.0)), (1, card("Four Drop", mv=4.0))])
    assert report_mv(deck) == pytest.approx(3.0)


def report_mv(deck):
    return audit(deck).average_mana_value


def test_average_mana_value_is_zero_for_an_all_land_deck():
    assert report_mv(build([(36, FOREST)])) == 0.0


def test_curve_buckets_nonland_cards_with_seven_plus_bucket():
    deck = build([
        (1, card("One", mv=1.0)), (1, card("Two", mv=2.0)),
        (1, card("Big", mv=9.0)), (36, FOREST),
    ])
    curve = dict(audit(deck).curve)
    assert curve[1] == 1
    assert curve[2] == 1
    assert curve[7] == 1  # the 7+ bucket
    assert 9 not in curve


def test_category_count_reports_target_band_and_status():
    deck = build([(36, FOREST), (1, SOL_RING)])
    report = audit(deck)
    ramp = next(c for c in report.categories if c.function is F.RAMP)
    assert ramp.count == 1
    assert (ramp.target_min, ramp.target_max) == targets.RAMP
    assert ramp.status == "low"


def test_category_status_ok_when_inside_band():
    deck = build([(36, FOREST)] + [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.",
                                            mv=2.0, cost="{2}", produced="C"))
                                   for i in range(11)])
    report = audit(deck)
    ramp = next(c for c in report.categories if c.function is F.RAMP)
    assert ramp.count == 11
    assert ramp.status == "ok"


def test_category_status_high_above_band():
    deck = build([(36, FOREST)] + [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.",
                                            mv=2.0, cost="{2}", produced="C"))
                                   for i in range(20)])
    ramp = next(c for c in audit(deck).categories if c.function is F.RAMP)
    assert ramp.status == "high"


def test_land_count_respects_quantities():
    assert audit(build([(36, FOREST)])).land_count == 36


def test_mana_sources_include_lands_ramp_and_mdfc():
    deck = build([(36, FOREST), (1, SOL_RING)])
    assert audit(deck).mana_sources == 37


def test_pip_report_counts_most_demanding_card():
    triple = card("Triple Green", oracle_text="", mv=3.0, cost="{G}{G}{G}")
    deck = build([(36, FOREST), (1, triple)])
    green = next(p for p in audit(deck).pips if p.color == "G")
    assert green.max_pips == 3
    assert green.sources == 36
    assert green.required == targets.PIP_SOURCE_MINIMUMS[3]
    assert green.ok is True


def test_pip_report_flags_insufficient_sources():
    triple = card("Triple Blue", oracle_text="", mv=3.0, cost="{U}{U}{U}", identity="U")
    deck = build([(36, FOREST), (1, triple)])
    blue = next(p for p in audit(deck).pips if p.color == "U")
    assert blue.sources == 0
    assert blue.ok is False


def test_pip_report_omits_unused_colors():
    deck = build([(36, FOREST), (1, card("Green Bear", cost="{1}{G}"))])
    assert {p.color for p in audit(deck).pips} == {"G"}


def test_generic_and_x_costs_are_not_pips():
    deck = build([(36, FOREST), (1, card("Xy", cost="{X}{5}", identity=""))])
    assert audit(deck).pips == ()


def test_colorless_and_snow_symbols_are_not_pips():
    deck = build([(36, FOREST),
                  (1, card("Rock", "Artifact", mv=2.0, cost="{C}{C}", identity="")),
                  (1, card("Snowy", "Artifact", mv=2.0, cost="{2}{S}", identity=""))])
    assert audit(deck).pips == ()


@pytest.mark.parametrize(
    "cost,color,expected_max_pips",
    [
        ("{2}{G/W}", "G", 1),
        ("{2}{G/W}", "W", 1),
        ("{G/P}", "G", 1),
        # Monocolored hybrid: the left side is generic. Reporting zero pips here
        # would understate the deck's need for white sources.
        ("{2/W}{2/W}{2/W}", "W", 3),
        ("{2/B}{2/B}{2/B}", "B", 3),
    ],
    ids=["hybrid-G", "hybrid-W", "phyrexian", "spectral-procession", "beseech"],
)
def test_hybrid_and_monocolored_hybrid_pips_are_counted(cost, color, expected_max_pips):
    identity = "".join(sorted({c for c in cost if c in "WUBRG"}))
    deck = build([(36, FOREST), (1, card("Hybrid Card", cost=cost, mv=3.0, identity=identity))])
    report = next(p for p in audit(deck).pips if p.color == color)
    assert report.max_pips == expected_max_pips


def test_audit_accepts_precomputed_tags():
    deck = build([(36, FOREST), (1, SOL_RING)])
    tags = {"Forest": frozenset({F.LAND}), "Sol Ring": frozenset({F.DRAW})}
    report = audit(deck, tags=tags)
    draw = next(c for c in report.categories if c.function is F.DRAW)
    assert draw.count == 1


def test_average_mv_band_flagged_when_out_of_range():
    deck = build([(36, FOREST)] + [(1, card(f"Fatty {i}", mv=8.0, cost="{8}"))
                                   for i in range(10)])
    report = audit(deck)
    assert report.average_mana_value > targets.AVERAGE_MV_BAND[1]
    assert report.curve_status == "high"


def test_category_delta_is_positive_when_under_target():
    """delta > 0 means 'add this many to reach the band'."""
    deck = build([(36, FOREST), (1, SOL_RING)])
    ramp = next(c for c in audit(deck).categories if c.function is F.RAMP)
    assert ramp.status == "low"
    assert ramp.delta == targets.RAMP[0] - ramp.count
    assert ramp.delta > 0


def test_category_delta_is_negative_when_over_target():
    """delta < 0 means 'cut this many to reach the band'."""
    rocks = [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.", mv=2.0, cost="{2}",
                      produced="C")) for i in range(20)]
    deck = build([(36, FOREST)] + rocks)
    ramp = next(c for c in audit(deck).categories if c.function is F.RAMP)
    assert ramp.status == "high"
    assert ramp.delta == targets.RAMP[1] - ramp.count
    assert ramp.delta < 0


def test_category_delta_is_zero_inside_the_band():
    rocks = [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.", mv=2.0, cost="{2}",
                      produced="C")) for i in range(11)]
    deck = build([(36, FOREST)] + rocks)
    ramp = next(c for c in audit(deck).categories if c.function is F.RAMP)
    assert ramp.status == "ok"
    assert ramp.delta == 0


@pytest.mark.parametrize("cost,pips", [("{W}{W}{W}{W}", 4), ("{W}{W}{W}{W}{W}", 5)])
def test_pip_requirement_clamps_above_the_table_maximum(cost, pips):
    """A 4- or 5-pip cost must clamp to the 3-pip requirement, not KeyError."""
    heavy = card("Heavy White", oracle_text="", mv=float(pips), cost=cost, identity="W")
    plains = card("Plains", "Basic Land — Plains", "({T}: Add {W}.)", mv=0.0, cost="",
                  produced="W", identity="W")
    deck = build([(36, plains), (1, heavy)])
    report = next(p for p in audit(deck).pips if p.color == "W")
    assert report.max_pips == pips
    assert report.required == targets.PIP_SOURCE_MINIMUMS[max(targets.PIP_SOURCE_MINIMUMS)]


def test_a_card_that_is_both_ramp_and_an_mdfc_back_counts_once():
    """No real card does this yet; the sum must not depend on that."""
    hybrid = card(
        "Ramp Front // Land Back",
        "Sorcery // Land",
        "Search your library for a basic land card, put it onto the battlefield, then shuffle.",
        mv=2.0, cost="{1}{G}", produced="G", identity="G",
    )
    deck = build([(36, FOREST), (1, hybrid)])
    report = audit(deck)
    assert report.land_count == 36
    assert report.mdfc_land_count == 1
    # 36 lands + 1 ramp + 1 MDFC back, minus the 1 overlap = 37, not 38.
    assert report.mana_sources == 37
