import pandas as pd
import pytest
import MetaTrader5 as mt5

from signal_generator.properties.signal_generator_properties import RSITrendProps
from signal_generator.signals.signal_rsi_trend import SignalRSITrend
from utils.config import FrameworkConfig
from test_signals import generate


def rsi_trend(trend_period=10):
    return SignalRSITrend(RSITrendProps(timeframe="1min", rsi_period=3, rsi_upper=70, rsi_lower=30, trend_period=trend_period,
                                        sl_points=50, tp_points=100))


# ---------------- RSI con filtro de tendencia
def test_buys_pullback_in_uptrend():
    # Sube con fuerza y retrocede en las últimas velas: RSI bajo, pero por encima de la media de 10 velas
    mt5.STATE["closes"] = [1.00 + i * 0.01 for i in range(30)] + [1.285, 1.28, 1.275]
    assert generate(rsi_trend()).signal == "BUY"


def test_does_not_buy_against_downtrend():
    # Cae de forma continua: el RSI está muy bajo (el RSI normal compraría), pero la tendencia es bajista
    mt5.STATE["closes"] = [1.30 - i * 0.01 for i in range(30)]
    assert generate(rsi_trend()) is None


def test_sells_rally_in_downtrend():
    mt5.STATE["closes"] = [1.30 - i * 0.01 for i in range(30)] + [1.015, 1.02, 1.025]
    assert generate(rsi_trend()).signal == "SELL"


def test_waits_for_enough_bars_for_the_trend():
    mt5.STATE["closes"] = [1.00 + i * 0.01 for i in range(30)] + [1.285, 1.28, 1.275]
    assert generate(rsi_trend(trend_period=200)) is None


def test_config_rsi_trend(tmp_path):
    text = open("config.toml", encoding="utf-8").read().replace('strategy = "rsi" ', 'strategy = "rsi_trend" ')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    config = FrameworkConfig(str(path))
    assert isinstance(config.signal_props, RSITrendProps) and config.signal_props.trend_period == 200


# ---------------- Pruebas en lote
def test_batch_runs_each_timeframe_and_period(tmp_path, monkeypatch):
    from backtest.batch import run_batch

    # Datos sintéticos de 1h en una carpeta temporal
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    import numpy as np
    rng = np.random.default_rng(3)
    times = pd.date_range("2025-01-01", "2025-01-25", freq="1h")
    for symbol, price in [("EURUSD", 1.10), ("USDJPY", 150.0), ("GBPUSD", 1.30)]:
        close = price * np.exp(np.cumsum(rng.normal(0, 0.001, len(times))))
        open_ = np.r_[price, close[:-1]]
        pd.DataFrame({"time": times.astype("int64") // 10**9, "open": open_, "high": np.maximum(open_, close) * 1.0005,
                      "low": np.minimum(open_, close) * 0.9995, "close": close, "spread": 5}).to_csv(data_dir / f"{symbol}_1h.csv", index=False)

    text = open("config.toml", encoding="utf-8").read()
    text = text.replace('start = "2025-01-01"', 'start = "2025-01-02"').replace('end = "2026-10-01"', 'end = "2025-01-25"')
    text = text.replace('data_dir = "backtest/data"', f'data_dir = "{data_dir.as_posix()}"')
    text = text.replace("sl_points = 50 ", "sl_points = 300 ").replace("tp_points = 100", "tp_points = 600")
    path = tmp_path / "pruebas.toml"
    path.write_text(text, encoding="utf-8")

    monkeypatch.chdir(tmp_path)   # los resultados se guardan en tmp_path/backtest/results
    rows = run_batch(FrameworkConfig(str(path)), ["15min", "1h"], split="2025-01-14")

    assert [(r["timeframe"], r["period"]) for r in rows] == [("15min", "práctica"), ("15min", "comprobación"),
                                                              ("1h", "práctica"), ("1h", "comprobación")]
    assert rows[0]["error"] == "sin datos"
    # Los dos backtests de 1h se ejecutan seguidos en el mismo proceso, cada uno con su periodo
    assert rows[2]["stats"]["trades"] > 0 and rows[3]["stats"]["trades"] > 0
    assert rows[2]["stats"]["first_bar"].startswith("2025-01-02") and rows[3]["stats"]["first_bar"].startswith("2025-01-14")
    assert len(list((tmp_path / "backtest" / "results").glob("batch_*_1h_*_trades.csv"))) == 2
