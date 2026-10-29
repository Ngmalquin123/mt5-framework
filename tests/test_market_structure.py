# Tests de la estructura de mercado (Fase 2). Los ejemplos son los de la especificación de la Fase 1.
import random
import pytest

from market_structure.structure import (StructureTracker, SwingEvent, BreakEvent, TrendEvent,
                                        BULLISH, BEARISH, RANGING)

POINT = 0.00001
M15, H1 = 900, 3600
T0 = 1_792_000_800      # una hora en punto


def run(bars, n=2, tf=M15, margin=5):
    """Pasa las velas (O, H, L, C) una a una. Devuelve el tracker y los eventos de cada vela."""
    tracker = StructureTracker(n, POINT, tf, margin)
    events = [tracker.update(T0 + i * tf, *bar) for i, bar in enumerate(bars)]
    return tracker, events


def of_type(events, cls):
    return [e for bar_events in events for e in bar_events if isinstance(e, cls)]


def zigzag(points, bars_per_leg=8):
    """Velas en línea recta entre los puntos (sin mechas): los giros son swings exactos con N <= 3."""
    bars = []
    for a, b in zip(points, points[1:]):
        step = (b - a) / bars_per_leg
        for k in range(bars_per_leg):
            o, c = a + step * k, a + step * (k + 1)
            bars.append(tuple(round(x, 5) for x in (o, max(o, c), min(o, c), c)))
    return bars


# ---------------- Sección 3 de la especificación: ejemplo alcista (EURUSD M15, N = 2) y su espejo bajista
BULL = [
    (1.1000, 1.1010, 1.0995, 1.1005), (1.1005, 1.1020, 1.1000, 1.1015), (1.1015, 1.1030, 1.1012, 1.1018),
    (1.1018, 1.1025, 1.1008, 1.1010), (1.1010, 1.1018, 1.1002, 1.1006), (1.1006, 1.1012, 1.0998, 1.1001),
    (1.1001, 1.1020, 1.1000, 1.1017), (1.1017, 1.1033, 1.1010, 1.1024), (1.1024, 1.1036, 1.1020, 1.1034),
]
BEAR = [
    (1.2700, 1.2705, 1.2690, 1.2695), (1.2695, 1.2700, 1.2680, 1.2685), (1.2685, 1.2688, 1.2670, 1.2682),
    (1.2682, 1.2692, 1.2675, 1.2690), (1.2690, 1.2698, 1.2682, 1.2694), (1.2694, 1.2702, 1.2688, 1.2699),
    (1.2699, 1.2700, 1.2680, 1.2683), (1.2683, 1.2690, 1.2667, 1.2676), (1.2676, 1.2680, 1.2664, 1.2666),
]


def test_bullish_example_swings_are_confirmed_n_bars_later():
    tracker, events = run(BULL)

    high, low = tracker.swings
    assert (high.kind, high.price, high.index, high.confirm_index) == ("high", 1.1030, 2, 4)
    assert (low.kind, low.price, low.index, low.confirm_index) == ("low", 1.0998, 5, 7)
    assert high.confirm_time == T0 + 5 * M15            # cierre de la vela 4 (SW-2)
    assert [e.swing for e in events[4] if isinstance(e, SwingEvent)] == [high]
    assert [e.swing for e in events[7] if isinstance(e, SwingEvent)] == [low]


def test_bullish_example_wick_is_not_a_break_and_close_is():
    tracker, events = run(BULL)

    assert of_type(events[:8], BreakEvent) == []          # vela 7: solo la mecha supera 1.1030
    (brk,) = of_type(events, BreakEvent)
    assert (brk.direction, brk.level, brk.index, brk.close, brk.origin) == (BULLISH, 1.1030, 8, 1.1034, 1.0998)
    # Sin swings anteriores no hay tendencia: la ruptura se registra, pero en rango (BOS-3)
    assert brk.kind == "BREAK"


def test_bearish_example_is_the_mirror():
    tracker, events = run(BEAR)

    assert [(s.kind, s.price, s.index, s.confirm_index) for s in tracker.swings] == [("low", 1.2670, 2, 4), ("high", 1.2702, 5, 7)]
    (brk,) = of_type(events, BreakEvent)
    assert (brk.direction, brk.level, brk.index, brk.origin) == (BEARISH, 1.2670, 8, 1.2702)


def test_close_inside_the_margin_is_not_a_break():
    bars = BULL[:8] + [(1.1024, 1.1036, 1.1020, 1.10304)]     # 1.10304 < 1.1030 + 5 puntos
    assert of_type(run(bars)[1], BreakEvent) == []


def test_level_breaks_only_once():
    bars = BULL + [(1.1034, 1.1040, 1.1030, 1.1038), (1.1038, 1.1045, 1.1035, 1.1042)]
    assert len(of_type(run(bars)[1], BreakEvent)) == 1    # BOS-2


# ---------------- Empates, alternancia y velas envolventes
def p(x):
    return round(1.1000 + x * 0.0001, 5)


def flat(high, low):
    """Vela con cierre en el medio."""
    mid = (p(high) + p(low)) / 2
    return (round(mid, 6), p(high), p(low), round(mid, 6))


def test_equal_highs_the_first_one_is_the_pivot():
    bars = [flat(10, 0), flat(12, 1), flat(15, 2), flat(15, 3), flat(13, 4), flat(12, 5)]
    tracker, _ = run(bars)
    assert [(s.kind, s.index) for s in tracker.swings] == [("high", 2)]       # SW-3


def test_consecutive_highs_keep_the_highest():
    # Mínimos siempre crecientes: no hay swing low entre los dos máximos
    highs = [10, 11, 15, 12, 11, 16, 13, 12]
    tracker, events = run([flat(h, i) for i, h in enumerate(highs)])

    assert [(s.kind, s.price) for s in tracker.swings] == [("high", p(16))]   # SW-4
    replaced = [e for e in of_type(events, SwingEvent) if e.replaced]
    assert replaced[0].replaced.price == p(15)


def test_lower_consecutive_high_is_ignored():
    highs = [10, 11, 15, 12, 11, 14, 13, 12]
    tracker, _ = run([flat(h, i) for i, h in enumerate(highs)])
    assert [(s.kind, s.price) for s in tracker.swings] == [("high", p(15))]


def test_outside_bar_adds_both_swings_keeping_the_alternation():
    # N = 1. La vela 3 es swing high y swing low a la vez, y el último swing era un high
    bars = [flat(10, 9), flat(12, 10), flat(11, 9.5), flat(13, 7), flat(12, 8)]
    tracker, _ = run(bars, n=1)
    assert [(s.kind, s.index) for s in tracker.swings] == [("high", 1), ("low", 3), ("high", 3)]   # SW-5


# ---------------- Tendencia, BOS y CHoCH (secciones 4 y 11 de la especificación, en H1 con N = 3)
def breaks(events):
    return [(e.kind, e.direction, e.level) for e in of_type(events, BreakEvent)]


def trends(events):
    return [(e.old, e.new) for e in of_type(events, TrendEvent)]


def test_h1_example_of_section_4():
    tracker, events = run(zigzag([1.1000, 1.0960, 1.1040, 1.0995, 1.1110, 1.0980]), n=3, tf=H1)

    assert [s.label for s in tracker.swings] == [None, None, "HL", "HH"]
    assert trends(events) == [(RANGING, BULLISH), (BULLISH, RANGING)]
    assert breaks(events) == [("BREAK", BULLISH, 1.1040), ("CHOCH", BEARISH, 1.0995)]


def test_image_1_bullish_structure():
    # LL, LH, LL → CHoCH → HH, HL (aquí se confirma la tendencia alcista) → BOS → HH, HL
    points = [1.1060, 1.1000, 1.1045, 1.0985, 1.1020, 1.0960, 1.1040, 1.0995, 1.1110, 1.1031, 1.1075]
    tracker, events = run(zigzag(points), n=3, tf=H1)

    assert [(s.price, s.label) for s in tracker.swings] == [
        (1.1000, None), (1.1045, None), (1.0985, "LL"), (1.1020, "LH"), (1.0960, "LL"),
        (1.1040, "HH"), (1.0995, "HL"), (1.1110, "HH"), (1.1031, "HL")]
    assert breaks(events) == [("BREAK", BEARISH, 1.1000), ("BOS", BEARISH, 1.0985),
                              ("CHOCH", BULLISH, 1.1020), ("BOS", BULLISH, 1.1040)]
    assert trends(events) == [(RANGING, BEARISH), (BEARISH, RANGING), (RANGING, BULLISH)]
    assert tracker.trend == BULLISH
    assert tracker.protected_swing().price == 1.0995               # TR-3

    # La tendencia alcista empieza al confirmarse el HL 1.0995, antes del BOS de 1.1040
    to_bullish = of_type(events, TrendEvent)[-1]
    hl = tracker.swings[6]
    bos = of_type(events, BreakEvent)[-1]
    assert to_bullish.index == hl.confirm_index < bos.index


def test_image_2_bearish_structure():
    # HH, HL, HH → CHoCH → LL, LH (aquí se confirma la tendencia bajista) → BOS → LL
    points = [1.2640, 1.2700, 1.2655, 1.2720, 1.2680, 1.2750, 1.2660, 1.2705, 1.2580, 1.2659, 1.2616]
    tracker, events = run(zigzag(points), n=3, tf=H1)

    assert [s.label for s in tracker.swings] == [None, None, "HH", "HL", "HH", "LL", "LH", "LL", "LH"]
    assert breaks(events) == [("BREAK", BULLISH, 1.2700), ("BOS", BULLISH, 1.2720),
                              ("CHOCH", BEARISH, 1.2680), ("BOS", BEARISH, 1.2660)]
    assert trends(events) == [(RANGING, BULLISH), (BULLISH, RANGING), (RANGING, BEARISH)]
    assert tracker.protected_swing().price == 1.2705


def test_bos_on_the_same_bar_that_confirms_the_trend():
    # En la Imagen 2 el LH 1.2705 se confirma en la misma vela que rompe 1.2660: es BOS, no ruptura en rango
    points = [1.2640, 1.2700, 1.2655, 1.2720, 1.2680, 1.2750, 1.2660, 1.2705, 1.2580, 1.2659, 1.2616]
    tracker, events = run(zigzag(points), n=3, tf=H1)
    bos = of_type(events, BreakEvent)[-1]
    lh = tracker.swings[6]
    assert bos.index == lh.confirm_index and bos.kind == "BOS"


def test_after_choch_the_trend_stays_ranging_until_new_structure():
    # TR-4: tras el CHoCH bajista las etiquetas aún dicen HH + HL, pero la tendencia queda lateral.
    # Vuelve a ser alcista solo con un HH nuevo (1.1130) y un HL (1.1050) confirmados después
    points = [1.1000, 1.0960, 1.1040, 1.0995, 1.1110, 1.0980, 1.1130, 1.1050, 1.1100]
    tracker, events = run(zigzag(points), n=3, tf=H1)

    assert trends(events) == [(RANGING, BULLISH), (BULLISH, RANGING), (RANGING, BULLISH)]
    choch = next(e for e in of_type(events, BreakEvent) if e.kind == "CHOCH")
    resumed = of_type(events, TrendEvent)[-1]
    new_hl = tracker.swings[-1]
    assert (new_hl.price, new_hl.label) == (1.1050, "HL")
    assert resumed.index == new_hl.confirm_index > choch.index


def test_after_choch_lh_and_ll_make_the_trend_bearish():
    points = [1.1000, 1.0960, 1.1040, 1.0995, 1.1110, 1.0980, 1.1060, 1.0940]
    _, events = run(zigzag(points), n=3, tf=H1)
    assert trends(events) == [(RANGING, BULLISH), (BULLISH, RANGING), (RANGING, BEARISH)]


# ---------------- Sin información futura (CV-5)
def random_bars(seed, count=2500):
    rng = random.Random(seed)
    price, bars = 1.1000, []
    for _ in range(count):
        o = price
        c = o + rng.gauss(0, 0.0004)
        h = max(o, c) + abs(rng.gauss(0, 0.0002))
        l = min(o, c) - abs(rng.gauss(0, 0.0002))
        bars.append(tuple(round(x, 5) for x in (o, h, l, c)))
        price = c
    return bars


@pytest.mark.parametrize("seed,n", [(1, 1), (2, 2), (3, 3)])
def test_no_lookahead_prefix_gives_the_same_events(seed, n):
    bars = random_bars(seed)
    _, full = run(bars, n=n)
    for cut in (50, 333, 1000, 1777, 2400):
        tracker, prefix = run(bars[:cut], n=n)
        assert prefix == full[:cut]


@pytest.mark.parametrize("seed,n", [(4, 2), (5, 3)])
def test_swings_and_breaks_match_the_definitions(seed, n):
    bars = random_bars(seed)
    highs, lows, closes = [b[1] for b in bars], [b[2] for b in bars], [b[3] for b in bars]
    tracker, events = run(bars, n=n)

    for s in tracker.swings:
        assert s.confirm_index == s.index + n
        values = highs if s.kind == "high" else lows
        sign = 1 if s.kind == "high" else -1
        assert all(sign * values[s.index] > sign * values[j] for j in range(s.index - n, s.index))
        assert all(sign * values[s.index] >= sign * values[j] for j in range(s.index + 1, s.index + n + 1))
    # Alternancia
    assert all(a.kind != b.kind for a, b in zip(tracker.swings, tracker.swings[1:]))

    for e in of_type(events, BreakEvent):
        assert e.index > e.swing.confirm_index                       # BOS-6
        if e.direction == BULLISH:
            assert closes[e.index] > e.level + 5 * POINT
        else:
            assert closes[e.index] < e.level - 5 * POINT
