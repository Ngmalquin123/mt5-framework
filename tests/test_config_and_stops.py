import pandas as pd
import pytest
import MetaTrader5 as mt5
from pydantic import ValidationError

from utils.config import FrameworkConfig
from signal_generator.properties.signal_generator_properties import RSIProps, MACrossoverProps
from signal_generator.signals.stop_levels import StopLevels
from position_sizer.position_sizer import PositionSizer
from position_sizer.properties.position_sizer_properties import FixedSizingProps
from events.events import SignalEvent
from conftest import drain


# ---------------- config.toml
def test_repo_config_keeps_current_settings():
    config = FrameworkConfig("config.toml")

    assert config.symbols == ["EURUSD", "USDJPY", "GBPUSD"] and config.timeframe == "1min"
    assert config.signal_props == RSIProps(timeframe="1min", rsi_period=5, rsi_upper=70, rsi_lower=30, sl_points=50, tp_points=100)
    assert config.sizing_props == FixedSizingProps(volume=0.05)
    assert config.risk_props.max_leverage_factor == 5
    assert config.trading_limits.max_daily_loss_pct == 0.02 and config.trading_limits.no_trading_start == "23:45"
    assert config.pending_order_max_age_minutes == 60
    assert config.backtest["initial_balance"] == 10000.0


def write_config(tmp_path, text: str) -> str:
    original = open("config.toml", encoding="utf-8").read()
    path = tmp_path / "config.toml"
    path.write_text(original.replace(*text) if isinstance(text, tuple) else original + text, encoding="utf-8")
    return str(path)


def test_switch_strategy_and_sizing(tmp_path):
    path = write_config(tmp_path, ('strategy = "rsi"', 'strategy = "ma_crossover"'))
    text = open(path, encoding="utf-8").read().replace('method = "fixed"', 'method = "risk_pct"')
    open(path, "w", encoding="utf-8").write(text)

    config = FrameworkConfig(path)
    assert isinstance(config.signal_props, MACrossoverProps) and config.signal_props.fast_period == 5
    assert config.sizing_props.risk_pct == 0.01


def test_unknown_strategy_is_reported(tmp_path):
    with pytest.raises(ValueError, match="Estrategia 'macd' no válida"):
        FrameworkConfig(write_config(tmp_path, ('strategy = "rsi"', 'strategy = "macd"')))


def test_typo_in_parameter_is_reported(tmp_path):
    with pytest.raises(ValidationError, match="sl_pionts"):
        FrameworkConfig(write_config(tmp_path, ("sl_points = 50 ", "sl_pionts = 50 ")))


# ---------------- SL/TP con puntos o ATR
TICK = {"bid": 1.1000, "ask": 1.1002}
POINT = 0.00001


def bars_with_range(bar_range: float, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({"high": [1.1 + bar_range / 2] * n, "low": [1.1 - bar_range / 2] * n, "close": [1.1] * n})


def test_atr_of_constant_range():
    assert StopLevels.compute_atr(bars_with_range(0.0020), 14) == pytest.approx(0.0020)


def test_atr_includes_gaps():
    bars = pd.DataFrame({"high": [1.1010, 1.1210], "low": [1.0990, 1.1190], "close": [1.1000, 1.1200]})
    # La segunda vela abre con un hueco de 200 pips: el True Range es desde el cierre anterior
    assert StopLevels.compute_atr(bars, 1) == pytest.approx(0.0210)


def test_stops_in_points():
    stops = StopLevels(RSIProps(timeframe="1min", rsi_period=5, rsi_upper=70, rsi_lower=30, sl_points=50, tp_points=100))
    sl, tp = stops.compute("BUY", TICK, POINT)
    assert sl == pytest.approx(1.0997) and tp == pytest.approx(1.1012)
    assert stops.bars_needed == 0


def test_stops_with_atr_adapt_to_volatility():
    stops = StopLevels(MACrossoverProps(timeframe="1h", fast_period=5, slow_period=10, sl_atr=1.5, tp_atr=3.0))
    assert stops.bars_needed == 15

    sl, tp = stops.compute("SELL", TICK, POINT, bars_with_range(0.0020))
    assert sl == pytest.approx(1.1030) and tp == pytest.approx(1.0940)

    # Con el doble de volatilidad, el doble de distancia
    sl, tp = stops.compute("SELL", TICK, POINT, bars_with_range(0.0040))
    assert sl == pytest.approx(1.1060) and tp == pytest.approx(1.0880)


def test_zero_disables_sl_or_tp():
    stops = StopLevels(MACrossoverProps(timeframe="1h", fast_period=5, slow_period=10, sl_atr=1.5))
    sl, tp = stops.compute("BUY", TICK, POINT, bars_with_range(0.0020))
    assert sl > 0 and tp == 0.0


def test_ma_crossover_with_atr_stops():
    from test_signals import generate
    from signal_generator.signals.signal_ma_crossover import SignalMACrossover

    mt5.STATE["closes"] = [1.10] * 20 + [1.12]
    signal = generate(SignalMACrossover(MACrossoverProps(timeframe="1min", fast_period=3, slow_period=5, sl_atr=1.5, tp_atr=3.0)))
    assert signal.signal == "BUY" and 0 < signal.sl < mt5.STATE["ask"] < signal.tp


# ---------------- Volumen ajustado al paso del símbolo
@pytest.mark.parametrize("volume, expected", [(0.05, 0.05), (0.30000000000000004, 0.3), (0.057, 0.05), (250.0, 100.0)])
def test_volume_is_normalized_to_step(volume, expected):
    assert PositionSizer._normalize_volume(volume, 0.01, 100.0) == expected


def test_sizer_sends_normalized_volume(events):
    PositionSizer(events, None, FixedSizingProps(volume=0.057)).size_signal(
        SignalEvent(symbol="EURUSD", signal="BUY", target_order="MARKET", target_price=0.0, magic_number=1, sl=0.0, tp=0.0))
    assert drain(events)[0].volume == 0.05
