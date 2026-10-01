from mtgpt.goldfish.mana import Unit, parse_cost, plan_payment

G, U, C = frozenset("G"), frozenset("U"), frozenset("C")


def test_parse_cost():
    assert parse_cost("{2}{G}{G}") == (2, (G, G))
    assert parse_cost("{X}{G}") == (0, (G,))
    assert parse_cost("{G/U}{B/P}{2/W}") == (0, (frozenset("GU"), frozenset("B"), frozenset("W")))
    assert parse_cost("{C}{S}") == (1, (C,))
    assert parse_cost("") == (0, ())
    assert parse_cost("{1}{R} // {3}") == (1, (frozenset("R"),))


def land(colors, ref):
    return Unit(frozenset(colors), "land", ref)


def test_pays_generic_from_anything():
    units = [land("G", 0), land("U", 1), Unit(C, "rock", 2)]
    assert sorted(plan_payment(units, 3, ())) == [0, 1, 2]
    assert plan_payment(units, 4, ()) is None


def test_matching_finds_payment_greedy_would_miss():
    # Both duals tie on rank and width, so the G/U dual is tried first for {G}.
    # Greedy would then have nothing left for {U}; matching moves {G} to the B/G dual.
    units = [land("GU", 0), land("BG", 1)]
    assert sorted(plan_payment(units, 0, (G, U))) == [0, 1]


def test_colorless_pip_needs_colorless():
    assert plan_payment([land("G", 0)], 0, (C,)) is None
    assert plan_payment([Unit(C, "rock", 0)], 0, (C,)) == [0]


def test_prefers_pool_and_saves_treasure():
    units = [Unit(frozenset("WUBRG"), "treasure", -1), Unit(G, "pool", 0), land("G", 0)]
    assert plan_payment(units, 1, ()) == [1]
    assert sorted(plan_payment(units, 2, ())) == [1, 2]
