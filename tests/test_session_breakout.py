from datetime import datetime, timezone
import pytest
import MetaTrader5 as mt5

from signal_generator.properties.signal_generator_properties import SessionBreakoutProps
from signal_generator.signals.signal_session_breakout import SignalSessionBreakout
from utils.config import FrameworkConfig
from test_signals import generate, MAGIC


def server_time(hour: int, minute: int) -> int:
    return int(datetime(2026, 10, 27, hour, minute, tzinfo=timezone.utc).timestamp())


def set_day(now_hour: int, now_minute: int, last_close: float):
    """Velas de 1 minuto desde las 00:30 hasta la hora actual. Rango 02:00-10:00 entre 1.095 y 1.105."""
    mt5.STATE["time"] = server_time(now_hour, now_minute)
    first_minute = 30
    closes = []
    for minute in range(first_minute, now_hour * 60 + now_minute):
        if 2 * 60 <= minute < 10 * 60:
            closes.append(1.095 if minute % 2 else 1.105)
        else:
            closes.append(1.100)
    closes[-1] = last_close
    mt5.STATE["closes"] = closes
    mt5.STATE["bid"], mt5.STATE["ask"] = last_close, round(last_close + 0.0002, 5)


def breakout(**kwargs):
    return SignalSessionBreakout(SessionBreakoutProps(timeframe="1min", **kwargs))


def test_buys_breakout_above_range():
    set_day(10, 30, last_close=1.106)
    signal = generate(breakout())

    assert signal.signal == "BUY"
    assert signal.sl == pytest.approx(1.095)                     # lado contrario del rango
    assert signal.tp == pytest.approx(1.1062 + 0.010)            # entrada + 1 altura del rango


def test_sells_breakout_below_range():
    set_day(11, 0, last_close=1.094)
    signal = generate(breakout(tp_range_mult=2.0))
    assert signal.signal == "SELL" and signal.sl == pytest.approx(1.105) and signal.tp == pytest.approx(1.094 - 0.020)


def test_no_signal_inside_range_or_outside_window():
    set_day(10, 30, last_close=1.100)
    assert generate(breakout()) is None          # dentro del rango

    set_day(9, 30, last_close=1.106)
    assert generate(breakout()) is None          # el rango aún no ha terminado

    set_day(14, 30, last_close=1.106)
    assert generate(breakout()) is None          # fuera de la ventana de entrada


def test_only_one_entry_per_symbol_and_day():
    strategy = breakout()
    set_day(10, 30, last_close=1.106)
    assert generate(strategy).signal == "BUY"
    set_day(11, 0, last_close=1.108)
    assert generate(strategy) is None


def test_closes_positions_at_close_time():
    set_day(22, 5, last_close=1.100)
    mt5.STATE["positions"] = [mt5.Position(7, MAGIC, mt5.POSITION_TYPE_BUY, "EURUSD", 0.05, 0.0, 0.0)]

    assert generate(breakout()) is None
    assert mt5.STATE["positions"] == []


def test_stops_by_atr_when_range_stops_disabled():
    set_day(10, 30, last_close=1.106)
    signal = generate(breakout(sl_at_range=False, tp_range_mult=0, sl_points=100, tp_points=200))
    assert signal.sl == pytest.approx(1.1062 - 0.001) and signal.tp == pytest.approx(1.1062 + 0.002)


def test_invalid_hours_and_timeframe():
    with pytest.raises(Exception, match="range_start < range_end"):
        breakout(range_start="10:00", range_end="02:00")
    with pytest.raises(Exception, match="intradía"):
        SignalSessionBreakout(SessionBreakoutProps(timeframe="1d"))


def test_config_session_breakout(tmp_path):
    text = open("config.toml", encoding="utf-8").read().replace('strategy = "rsi" ', 'strategy = "session_breakout" ')
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    props = FrameworkConfig(str(path)).signal_props
    assert isinstance(props, SessionBreakoutProps) and props.range_end == "10:00" and props.sl_at_range is True
