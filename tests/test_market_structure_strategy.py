# Tests de la estrategia market_structure (Fase 3) con el backtest completo: velas M5 sintéticas que reproducen
# las imágenes 1 y 2 de la especificación (sección 11) y las variantes que deben descartar (sección 14).
# Las horas son del servidor. 2026-09-14 es lunes y la entrada es el jueves 17 (semana sin desfase horario).
from datetime import datetime, timedelta, timezone
import csv
import os
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


def backtest(tmp_path, monkeypatch, data: dict) -> dict:
    """Backtest completo con estructura.toml y los datos dados ({símbolo: velas M5})."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    for symbol, bars in data.items():
        bars.to_csv(data_dir / f"{symbol}_5min.csv", index=False)

    text = open("estructura.toml", encoding="utf-8").read()
    text = text.replace('symbols = ["EURUSD", "GBPUSD"]', f"symbols = {list(data)}".replace("'", '"'))
    text = text.replace('start = "2025-01-01"', 'start = "2026-09-14"').replace('end = "2026-10-01"', 'end = "2026-09-19"')
    text = text.replace('data_dir = "backtest/data"', f'data_dir = "{data_dir.as_posix()}"')
    path = tmp_path / "estructura.toml"
    path.write_text(text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    stats = run_backtest(FrameworkConfig(str(path)), results_name="test")
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
