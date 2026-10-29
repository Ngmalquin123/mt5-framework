# Tests de la estrategia market_structure (Fase 3) con el backtest completo: velas M5 sintéticas que reproducen
# las imágenes 1 y 2 de la especificación (sección 11) y las variantes que deben descartar (sección 14).
# Las horas son del servidor. 2026-09-14 es lunes y la entrada es el jueves 17 (semana sin desfase horario).
from datetime import datetime, timedelta, timezone
import csv
import json
import os
import re
import pandas as pd
import pytest

from utils.config import FrameworkConfig
from backtest.runner import run_backtest

M5 = 300
H1_LEG_HOURS = 8
START = datetime(2026, 9, 14, 10)

# Panel A de cada imagen: swings de H1 (cada tramo dura 8 h) hasta el máximo/mínimo del jueves a las 02:00
BULL_H1 = [1.1060, 1.1000, 1.1045, 1.0985, 1.1020, 1.0960, 1.1040, 1.0995, 1.1110]
BEAR_H1 = [1.2640, 1.2700, 1.2655, 1.2720, 1.2680, 1.2750, 1.2660, 1.2705, 1.2580]

# Panel B de la imagen 1 (jueves): ruptura M15 de 1.1062, toque de la zona, L1 → Hc → L2 en M5 y vela de señal 11:50
BULL_DAY = [("07:30", 1.1085), ("07:55", 1.1050), ("08:15", 1.1045), ("08:45", 1.1062), ("09:15", 1.1050), ("09:30", 1.1031),
            ("10:10", 1.1068), ("10:30", 1.1078), ("10:50", 1.1063), ("11:05", 1.1058), ("11:20", 1.1071), ("11:35", 1.1064),
            ("11:50", 1.1074), ("12:40", 1.1092), ("13:20", 1.1105), ("13:30", 1.1104)]
# Imagen 2: el espejo exacto (1.1062 ↔ 1.2628, 1.1031 ↔ 1.2659, 1.1074 ↔ 1.2616...)
MIRROR = 2.3690
BEAR_DAY = [(t, round(MIRROR - p, 5)) for t, p in BULL_DAY]


def server(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def waypoints(h1_points: list, day: list, shift_minutes: int = 0) -> list:
    points = [(START + timedelta(hours=H1_LEG_HOURS * i), p) for i, p in enumerate(h1_points)]
    points += [(datetime.strptime(f"2026-09-17 {t}", "%Y-%m-%d %H:%M"), p) for t, p in day]
    return [(int((t + timedelta(minutes=shift_minutes)).replace(tzinfo=timezone.utc).timestamp()), p) for t, p in points]


def m5_bars(points: list, spread: int = 10) -> pd.DataFrame:
    """Velas M5 que siguen el camino en línea recta entre los puntos: el cierre de la vela T es el precio en T."""
    rows, previous = [], points[0][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        for t in range(t0 if not rows else t0 + M5, t1 + 1, M5):
            close = round(p0 + (p1 - p0) * (t - t0) / (t1 - t0), 5)
            rows.append({"time": t, "open": previous, "high": max(previous, close), "low": min(previous, close), "close": close,
                         "tick_volume": 1, "spread": spread, "real_volume": 0})
            previous = close
    return pd.DataFrame(rows)


def write_config(tmp_path, data: dict, strategy: dict = None) -> str:
    """Escribe las velas M5 ({símbolo: velas}) y una copia de estructura.toml que las usa. strategy: parámetros que se cambian."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    for symbol, bars in data.items():
        bars.to_csv(data_dir / f"{symbol}_5min.csv", index=False)

    text = open("estructura.toml", encoding="utf-8").read()
    text = text.replace('symbols = ["EURUSD", "GBPUSD"]', f"symbols = {list(data)}".replace("'", '"'))
    text = text.replace('start = "2025-01-01"', 'start = "2026-09-14"').replace('end = "2026-10-01"', 'end = "2026-09-19"')
    text = text.replace('data_dir = "backtest/data"', f'data_dir = "{data_dir.as_posix()}"')
    for key, value in (strategy or {}).items():
        line = f"{key} = {json.dumps(value)}"
        text, found = re.subn(rf"^{key} = .*$", line, text, flags=re.M)
        if not found:
            text = text.replace("warmup_days = 30", f"warmup_days = 30\n{line}")
    path = tmp_path / "estructura.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def backtest(tmp_path, monkeypatch, data: dict, strategy: dict = None) -> dict:
    """Backtest completo con estructura.toml y los datos dados ({símbolo: velas M5}). strategy: parámetros que se cambian."""
    path = write_config(tmp_path, data, strategy)
    monkeypatch.chdir(tmp_path)
    stats = run_backtest(FrameworkConfig(path), results_name="test")
    stats["trade_rows"] = []
    if os.path.exists(stats["trades_file"]):           # sin operaciones no se crea el archivo
        with open(stats["trades_file"], encoding="utf-8") as f:
            stats["trade_rows"] = list(csv.DictReader(f))
    return stats


def reasons(stats: dict) -> list:
    return [row["reason"] for row in stats["decisions"]]


def has_reason(stats: dict, prefix: str) -> bool:
    return any(reason.startswith(prefix) for reason in reasons(stats))


# ---------------- Imagen 1: compra en EURUSD
def test_image_1_buy(tmp_path, monkeypatch):
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))})

    (trade,) = stats["trade_rows"]                      # una sola operación
    assert trade["side"] == "BUY" and trade["entry_time"] == "2026-09-17 11:55:00"
    assert float(trade["entry_price"]) == 1.1075        # Ask en la apertura de la vela siguiente a la señal (EN-1)
    assert float(trade["volume"]) == 0.38               # 50 USD / 130 USD por lote, hacia abajo (RG-1)
    assert trade["exit_reason"] == "TP" and float(trade["exit_price"]) == 1.1101

    sent = [r for r in stats["decisions"] if r["reason"].startswith("EN-1")]
    assert [r["time"] for r in sent] == ["2026-09-17 11:55"]
    assert sent[0]["reason"] == "EN-1: BUY a 1.10750, SL 1.10620, TP 1.11010"
    signal = next(r for r in stats["decisions"] if r["reason"].startswith("ST-8"))
    assert signal["time"] == "2026-09-17 11:55"          # cierre de la vela de las 11:50
    assert "1.10710" in signal["reason"] and "HL 1.10640" in signal["reason"]


def test_image_1_steps_of_the_state_machine(tmp_path, monkeypatch):
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))})
    thursday = [(r["time"][11:], r["to"], r["reason"].split(":")[0]) for r in stats["decisions"] if r["time"] >= "2026-09-17 09:00"]

    assert thursday[:5] == [("10:15", "ESPERANDO_RETROCESO", "ST-2"),          # ① cierre M15 sobre 1.1062
                            ("10:55", "ESPERANDO_CONFIRMACION", "ST-4"),       # ② la vela de las 10:50 toca la zona
                            ("11:55", "SENAL", "ST-8"),                        # ⑥ vela de las 11:50
                            ("11:55", "SENAL", "EN-1"),
                            ("12:00", "EN_POSICION", "EN-4")]
    assert thursday[5][1:] == ("ESPERANDO_RUPTURA", "ST-11")                   # TP: vuelta a esperar una ruptura nueva
    ruptura = next(r["reason"] for r in stats["decisions"] if r["time"] == "2026-09-17 10:15")
    assert "1.10620" in ruptura and "origen 1.10310" in ruptura

    # Antes, H1: bajista → lateral por el CHoCH → alcista al confirmarse el HL 1.0995 (TR-4)
    h1 = [r["reason"] for r in stats["decisions"] if r["reason"].startswith(("ST-1:", "TR-4"))]
    assert h1 == ["ST-1: H1 bajista", "TR-4/CH-2: H1 lateral", "ST-1: H1 alcista"]


# ---------------- Imagen 2: venta en GBPUSD
def test_image_2_sell(tmp_path, monkeypatch):
    stats = backtest(tmp_path, monkeypatch, {"GBPUSD": m5_bars(waypoints(BEAR_H1, BEAR_DAY))})

    (trade,) = stats["trade_rows"]
    assert trade["side"] == "SELL" and trade["entry_time"] == "2026-09-17 11:55:00"
    assert float(trade["entry_price"]) == 1.2616        # Bid
    assert float(trade["volume"]) == 0.38
    assert trade["exit_reason"] == "TP" and float(trade["exit_price"]) == 1.2590
    sent = next(r["reason"] for r in stats["decisions"] if r["reason"].startswith("EN-1"))
    assert sent == "EN-1: SELL a 1.26160, SL 1.26290, TP 1.25900"   # SL = LH 1.2626 + 2 pips + 1 pip de spread


# ---------------- Variantes que deben descartar (sección 14), cada una con su motivo
def test_h1_obstacle_before_the_take_profit(tmp_path, monkeypatch):
    # El LL de H1 en 1.2600 en lugar de 1.2580: queda antes del TP 1.2590 (TP-2)
    day = [("07:30", 1.2605)] + BEAR_DAY[1:]
    stats = backtest(tmp_path, monkeypatch, {"GBPUSD": m5_bars(waypoints(BEAR_H1[:-1] + [1.2600], day))})
    assert stats["trade_rows"] == []
    assert has_reason(stats, "TP-2: swing low de H1 en 1.26000 antes del TP 1.25900")


def test_close_beyond_the_origin_cancels(tmp_path, monkeypatch):
    # Después del toque, el precio cierra por debajo del origen 1.1031 (ST-6)
    day = [p for p in BULL_DAY if p[0] <= "10:50"] + [("11:05", 1.1025), ("11:30", 1.1028)]
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, day))})
    assert stats["trade_rows"] == []
    assert has_reason(stats, "ST-6: cierre M5 más allá del origen")


def test_zone_not_touched_in_time(tmp_path, monkeypatch):
    # El precio no vuelve a la zona hasta después de las 14:15 (16 velas M15 desde la ruptura)
    day = [p for p in BULL_DAY if p[0] <= "10:30"] + [("14:10", 1.1080), ("14:40", 1.1063), ("14:45", 1.1063)]
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, day))})
    assert stats["trade_rows"] == []
    expired = next(r for r in stats["decisions"] if r["reason"].startswith("ST-5"))
    assert expired["time"] == "2026-09-17 14:15"


def test_signal_outside_the_entry_windows(tmp_path, monkeypatch):
    # Todo 3 horas más tarde: la entrada sería a las 14:55 del servidor, entre Londres (hasta 14:00) y Nueva York (desde 15:00)
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY, shift_minutes=180))})
    assert stats["trade_rows"] == []
    assert has_reason(stats, "HO-2")


def test_second_signal_with_a_position_open_in_another_pair(tmp_path, monkeypatch):
    # GBPUSD da su señal 20 minutos después, con la compra de EURUSD todavía abierta (RG-2)
    data = {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY)),
            "GBPUSD": m5_bars(waypoints(BEAR_H1, BEAR_DAY, shift_minutes=20))}
    stats = backtest(tmp_path, monkeypatch, data)
    assert [t["symbol"] for t in stats["trade_rows"]] == ["EURUSD"]
    rejected = [r for r in stats["decisions"] if r["reason"].startswith("RG-2")]
    assert [(r["time"], r["symbol"]) for r in rejected] == [("2026-09-17 12:15", "GBPUSD")]


def test_two_signals_on_the_same_bar_the_first_symbol_wins(tmp_path, monkeypatch):
    data = {"GBPUSD": m5_bars(waypoints(BEAR_H1, BEAR_DAY)), "EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))}
    stats = backtest(tmp_path, monkeypatch, data)
    assert [t["symbol"] for t in stats["trade_rows"]] == ["GBPUSD"]          # el primero de symbols
    assert [r["symbol"] for r in stats["decisions"] if r["reason"].startswith("RG-2")] == ["EURUSD"]


def test_stop_loss_smaller_than_three_spreads(tmp_path, monkeypatch):
    # Con 7 pips de spread el SL (19 pips desde el Ask) es menor que 3 × spread = 21 pips (SL-2)
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY), spread=70)})
    assert stats["trade_rows"] == []
    assert has_reason(stats, "SL-2: SL de 190 puntos, menor que el mínimo de 210")


# ---------------- Horario y registro
def test_intraday_close_at_16h_new_york(tmp_path, monkeypatch):
    # Sin llegar al TP, la compra se cierra a las 23:00 del servidor (16:00 de Nueva York, HO-3)
    day = [p for p in BULL_DAY if p[0] <= "11:50"] + [("12:40", 1.1085), ("23:30", 1.1086)]
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, day))})
    (trade,) = stats["trade_rows"]
    assert trade["exit_time"].startswith("2026-09-17 23:00") and trade["exit_reason"] not in ("SL", "TP")


def test_report_says_there_is_no_news_filter(tmp_path, monkeypatch, capsys):
    backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))})
    out = capsys.readouterr().out
    assert "Filtro de noticias: DESACTIVADO" in out
    assert "señales: 1 | enviadas: 1 | ejecutadas: 1" in out


def test_needs_5min():
    from signal_generator.properties.signal_generator_properties import MarketStructureProps
    from signal_generator.signals.signal_market_structure import SignalMarketStructure
    with pytest.raises(Exception, match="5min"):
        SignalMarketStructure(MarketStructureProps(timeframe="15min"))
    with pytest.raises(Exception):
        SignalMarketStructure(MarketStructureProps(timeframe="5min", sessions=[("Europe/Londres", "08:00", "12:00")]))


def test_analysis_of_the_backtest(tmp_path, monkeypatch, capsys):
    # backtest.analyze_trades lee los dos CSV: la operación de la imagen 1 es un TP de 2R en la sesión de Londres
    from backtest.analyze_trades import analyze
    backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))})
    tables = analyze("test", "backtest/results", "estructura.toml")

    (trade,) = tables["trades"].to_dict("records")
    assert trade["sl"] == 1.1062 and trade["r"] == pytest.approx(2.0)
    assert (trade["año"], trade["sesión"], trade["hora NY"], trade["día"]) == (2026, "Londres", "04h", "jueves")
    assert tables["exit_reason"].loc["TP", "ops"] == 1 and tables["exit_reason"].loc["TP", "R total"] == 2.0
    funnel = tables["funnel"].loc["EURUSD"]
    assert (funnel["señales"], funnel["enviadas"], funnel["ejecutadas"]) == (1, 1, 1)
    assert "1 operaciones" in capsys.readouterr().out


def test_position_opened_and_closed_in_the_same_bar(tmp_path, monkeypatch):
    # La vela de la entrada (11:55) baja hasta 1.1040: el SL 1.1062 salta dentro de ella y la posición nunca se ve abierta.
    # Es una orden ejecutada (EN-4) y cerrada (ST-11), no una señal que no llegó a abrir posición
    day = [p for p in BULL_DAY if p[0] <= "11:50"] + [("11:55", 1.1040), ("13:00", 1.1030)]
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, day))})
    (trade,) = stats["trade_rows"]
    assert trade["entry_time"] == "2026-09-17 11:55:00" and trade["exit_reason"] == "SL"
    assert has_reason(stats, "EN-4") and has_reason(stats, "ST-11: posición cerrada en la misma vela")
    assert not has_reason(stats, "EN-3/EN-4")


# ---------------- Variantes de la sección 16 de la especificación (cada una cambia una sola regla)
V1 = {"stop_mode": "m15_origin", "rr": 1.5, "max_sl_points": 600}


def test_variant_v1_stop_beyond_the_m15_origin(tmp_path, monkeypatch):
    # SL = origen 1.1031 − 2 pips = 1.1029: 460 puntos desde 1.1075 y TP a 1.5 × 460 = 1.1144, más allá del máximo de H1 1.1110
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))}, V1)
    assert stats["trade_rows"] == []
    assert "TP-2: swing high de H1 en 1.11100 antes del TP 1.11440" in reasons(stats)


def test_variant_v1_sell(tmp_path, monkeypatch):
    # Venta: SL = origen 1.2659 + 2 pips + spread = 1.2662; TP 1.2616 − 1.5 × 460 puntos = 1.2547, más allá del mínimo de H1 1.2580
    stats = backtest(tmp_path, monkeypatch, {"GBPUSD": m5_bars(waypoints(BEAR_H1, BEAR_DAY))}, V1)
    assert stats["trade_rows"] == []
    assert "TP-2: swing low de H1 en 1.25800 antes del TP 1.25470" in reasons(stats)


def test_variant_v2_only_bos(tmp_path, monkeypatch):
    # La ruptura de 1.1062 es una ruptura en rango de M15 (BREAK): con V2 no abre setup. Sí cuenta el BOS de 1.1078
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))}, {"break_kinds": "bos"})
    assert stats["trade_rows"] == []
    assert not any("1.10620" in reason for reason in reasons(stats))
    assert "ST-2: ruptura M15 BOS de 1.10780 (origen 1.10580)" in reasons(stats)


def test_variant_v3_m15_confirmation(tmp_path, monkeypatch):
    # Tras el toque de las 10:55, la vela M15 de las 11:15 cierra en 1.10687 > 1.10623 (máximo de la vela M15 anterior) + margen.
    # SL detrás del mínimo desde el toque (1.1058 − 2 pips); compra al Ask de la vela siguiente
    stats = backtest(tmp_path, monkeypatch, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))}, {"confirmation": "m15_close"})
    (trade,) = stats["trade_rows"]
    assert trade["side"] == "BUY" and trade["entry_time"] == "2026-09-17 11:30:00"
    assert float(trade["volume"]) == 0.36 and trade["exit_reason"] == "TP"
    assert "EN-1: BUY a 1.10697, SL 1.10560, TP 1.10971" in reasons(stats)
    assert not has_reason(stats, "ST-8: cierre 1.10740")      # la secuencia de M5 ya no se usa


def test_variant_v3_sell(tmp_path, monkeypatch):
    stats = backtest(tmp_path, monkeypatch, {"GBPUSD": m5_bars(waypoints(BEAR_H1, BEAR_DAY))}, {"confirmation": "m15_close"})
    (trade,) = stats["trade_rows"]
    assert trade["side"] == "SELL" and trade["exit_reason"] == "TP"
    assert "EN-1: SELL a 1.26213, SL 1.26350, TP 1.25939" in reasons(stats)


def test_variant_values_are_checked():
    from pydantic import ValidationError
    from signal_generator.properties.signal_generator_properties import MarketStructureProps
    with pytest.raises(ValidationError):
        MarketStructureProps(timeframe="5min", stop_mode="m15")


def test_variants_development_and_single_check(tmp_path, monkeypatch, capsys):
    # backtest.variants con la imagen 1 en una semana: V0 y V3 ganan 2R; V1 (TP-2) y V2 (sin BOS) no operan.
    # V0 es solo la referencia: se elige V3. La comprobación se hace una vez y la segunda devuelve lo guardado
    import backtest.variants as variants
    path = write_config(tmp_path, {"EURUSD": m5_bars(waypoints(BULL_H1, BULL_DAY))})
    monkeypatch.chdir(tmp_path)
    week = ("2026-09-14", "2026-09-19")
    monkeypatch.setattr(variants, "PERIODS", {"desarrollo": week, "comprobacion": week})
    monkeypatch.setattr(variants, "MIN_TRADES_DEV", 1)
    monkeypatch.setattr(variants, "MIN_TRADES_CHECK", 1)
    os.makedirs("backtest/results", exist_ok=True)

    assert variants.development(path, "backtest/results") == "V3"
    rows = json.load(open("backtest/results/variantes_seleccion.json", encoding="utf-8"))["resultados"]
    assert [(r["variante"], r["ops"], r["R medio"]) for r in rows] == [("V0", 1, 2.0), ("V1", 0, 0.0), ("V2", 0, 0.0), ("V3", 1, 2.0)]

    assert variants.check(path, "backtest/results") is True
    monkeypatch.setattr(variants, "run_variant", lambda *args: pytest.fail("la comprobación no se puede repetir"))
    assert variants.check(path, "backtest/results") is True
    assert "ya se hizo" in capsys.readouterr().out


def test_variants_selection_criteria():
    from backtest.variants import select_variant, passes_check
    rows = [{"variante": "V0", "ops": 90, "R medio": 0.5}, {"variante": "V1", "ops": 49, "R medio": 0.4},
            {"variante": "V2", "ops": 60, "R medio": 0.12}, {"variante": "V3", "ops": 80, "R medio": 0.09}]
    assert select_variant(rows) == "V2"         # V0 no cuenta, V1 tiene pocas operaciones y V3 no llega a +0.10
    assert select_variant(rows[:2]) is None
    assert passes_check({"ops": 30, "R medio": 0.10, "PF": 1.10})
    assert not passes_check({"ops": 30, "R medio": 0.10, "PF": 1.09})
