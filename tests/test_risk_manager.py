from datetime import datetime, timezone
import MetaTrader5 as mt5
from conftest import drain

from data_provider.data_provider import DataProvider
from events.events import SizingEvent, OrderEvent
from portfolio.portfolio import Portfolio
from risk_manager.risk_manager import RiskManager
from risk_manager.properties.risk_manager_properties import MaxLeverageFactorRiskProps, TradingLimitsProps

MAGIC = 12345
SIZING = SizingEvent(symbol="EURUSD", signal="BUY", target_order="MARKET", target_price=0.0,
                     magic_number=MAGIC, sl=0.0, tp=0.0, volume=0.05)


def assess(events, limits=None) -> list:
    risk_manager = RiskManager(events, DataProvider(events, ["EURUSD"], "1min"), Portfolio(MAGIC),
                               MaxLeverageFactorRiskProps(max_leverage_factor=5), trading_limits=limits)
    risk_manager.assess_order(SIZING)
    return drain(events)


def set_server_time(hour: int, minute: int):
    mt5.STATE["time"] = int(datetime(2026, 10, 27, hour, minute, tzinfo=timezone.utc).timestamp())


def test_without_limits_order_goes_through(events):
    assert [type(e) for e in assess(events)] == [OrderEvent]


def test_default_limits_allow_normal_order(events):
    set_server_time(12, 0)
    assert [type(e) for e in assess(events, TradingLimitsProps())] == [OrderEvent]


def test_daily_loss_limit_blocks_new_orders(events):
    set_server_time(12, 0)
    # Hoy se han perdido 150 en operaciones cerradas y 100 en posiciones abiertas: 250 / 10250 = 2.4%
    mt5.STATE["deals"] = [mt5.Deal(0, MAGIC, -150.0, 0.0, 0.0), mt5.Deal(0, 999, -5000.0, 0.0, 0.0)]
    mt5.STATE["balance"] = 9850.0
    mt5.STATE["positions"] = [mt5.Position(1, MAGIC, mt5.POSITION_TYPE_SELL, "USDJPY", 0.01, -100.0, 0.0)]

    assert assess(events, TradingLimitsProps(max_daily_loss_pct=0.02)) == []


def test_max_open_positions(events):
    set_server_time(12, 0)
    mt5.STATE["positions"] = [mt5.Position(i, MAGIC, mt5.POSITION_TYPE_BUY, "GBPUSD", 0.01, 0.0, 0.0) for i in range(3)]
    assert assess(events, TradingLimitsProps(max_open_positions=3)) == []


def test_max_spread(events):
    set_server_time(12, 0)
    mt5.STATE["spread"] = 35
    assert assess(events, TradingLimitsProps(max_spread_points=20)) == []


def test_no_trading_window_crossing_midnight(events):
    limits = TradingLimitsProps(no_trading_start="23:45", no_trading_end="01:15")

    for hour, minute in [(23, 50), (0, 30), (1, 0)]:
        set_server_time(hour, minute)
        assert assess(events, limits) == [], f"{hour}:{minute} debería estar bloqueado"

    for hour, minute in [(1, 15), (12, 0), (23, 44)]:
        set_server_time(hour, minute)
        assert len(assess(events, limits)) == 1, f"{hour}:{minute} debería estar permitido"


def test_zero_disables_limits(events):
    set_server_time(0, 0)
    mt5.STATE["spread"] = 500
    limits = TradingLimitsProps(max_daily_loss_pct=0, max_open_positions=0, max_spread_points=0, no_trading_start="", no_trading_end="")
    assert len(assess(events, limits)) == 1
