import queue
import MetaTrader5 as mt5

from data_provider.data_provider import DataProvider
from events.events import DataEvent
from order_executor.order_executor import OrderExecutor
from portfolio.portfolio import Portfolio
from signal_generator.properties.signal_generator_properties import MACrossoverProps, RSIProps
from signal_generator.signals.signal_ma_crossover import SignalMACrossover
from signal_generator.signals.signal_rsi_mr import SignalRSI

MAGIC = 12345


def generate(strategy):
    events = queue.Queue()
    data_provider = DataProvider(events, ["EURUSD"], "1min")
    portfolio = Portfolio(MAGIC)
    bar = data_provider.get_latest_closed_bar("EURUSD", "1min")
    return strategy.generate_signal(DataEvent(symbol="EURUSD", data=bar), data_provider, portfolio, OrderExecutor(events, portfolio))


def rsi_strategy():
    return SignalRSI(RSIProps(timeframe="1min", rsi_period=5, rsi_upper=70, rsi_lower=30, sl_points=50, tp_points=100))


def ma_strategy():
    return SignalMACrossover(MACrossoverProps(timeframe="1min", fast_period=3, slow_period=5))


def test_rsi_sells_when_price_only_goes_up():
    # Antes el RSI salía 0 sin pérdidas y daba COMPRA en plena subida
    signal = generate(rsi_strategy())
    assert signal.signal == "SELL"
    assert signal.sl > mt5.STATE["bid"] > signal.tp


def test_rsi_buys_when_price_only_goes_down():
    mt5.STATE["closes"] = list(reversed(mt5.STATE["closes"]))
    assert generate(rsi_strategy()).signal == "BUY"


def test_rsi_without_enough_bars_returns_none():
    mt5.STATE["closes"] = [1.1, 1.2]
    assert generate(rsi_strategy()) is None


def test_ma_crossover_signal_only_on_the_cross_bar():
    # Precio plano y en la última vela un salto hacia arriba: la media rápida cruza a la lenta
    mt5.STATE["closes"] = [1.10] * 10 + [1.12]
    assert generate(ma_strategy()).signal == "BUY"

    # En la vela siguiente la media rápida sigue por encima, pero ya no hay cruce: no hay nueva señal
    mt5.STATE["closes"] = [1.10] * 9 + [1.12, 1.12]
    assert generate(ma_strategy()) is None


def test_ma_crossover_sell_on_cross_down():
    mt5.STATE["closes"] = [1.10] * 10 + [1.08]
    assert generate(ma_strategy()).signal == "SELL"


def test_does_not_open_if_opposite_position_cannot_be_closed():
    # Hay una compra abierta, llega señal de venta, pero el broker rechaza el cierre
    mt5.STATE["positions"] = [mt5.Position(7, MAGIC, mt5.POSITION_TYPE_BUY, "EURUSD", 0.05, 0.0, 0.0)]
    mt5.STATE["send_retcode"] = mt5.TRADE_RETCODE_REJECT

    assert generate(rsi_strategy()) is None
    assert len(mt5.STATE["positions"]) == 1


def test_opens_after_closing_opposite_position():
    mt5.STATE["positions"] = [mt5.Position(7, MAGIC, mt5.POSITION_TYPE_BUY, "EURUSD", 0.05, 0.0, 0.0)]

    assert generate(rsi_strategy()).signal == "SELL"
    assert mt5.STATE["positions"] == []
