import queue
import numpy as np
import pandas as pd
import pytest

from backtest.simulated_mt5 import SimulatedMT5
from backtest.backtester import Backtester

H = 3600
START = 1_735_689_600   # 2025-01-01 00:00 UTC
MAGIC = 12345


def make_bars(ohlc: list, spread: int = 10) -> pd.DataFrame:
    """Velas H1 consecutivas a partir de (open, high, low, close)."""
    return pd.DataFrame([{"time": START + i * H, "open": o, "high": h, "low": l, "close": c, "tick_volume": 1, "spread": spread, "real_volume": 0}
                         for i, (o, h, l, c) in enumerate(ohlc)])


def flat(price: float, n: int) -> list:
    return [(price, price, price, price)] * n


@pytest.fixture
def make_sim():
    """Crea brokers simulados activados y los desactiva al terminar el test."""
    sims = []

    def factory(bars: dict, **kwargs) -> SimulatedMT5:
        sim = SimulatedMT5(symbols=list(bars), timeframe="1h", bars=bars, specs={}, **kwargs)
        sim.activate()
        sims.append(sim)
        return sim

    yield factory
    for sim in reversed(sims):
        sim.deactivate()


def buy(sim: SimulatedMT5, symbol="EURUSD", sl=0.0, tp=0.0, volume=0.1):
    return sim.order_send({"action": sim.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume, "type": sim.ORDER_TYPE_BUY,
                           "sl": sl, "tp": tp, "magic": MAGIC})


# ---------------- Broker simulado
def test_strategy_only_sees_closed_bars(make_sim):
    sim = make_sim({"EURUSD": make_bars([(1.1 + i / 1000,) * 4 for i in range(10)])})
    sim.step()
    sim.step()
    sim.step()

    rates = sim.copy_rates_from_pos("EURUSD", sim.TIMEFRAME_H1, 1, 100)
    assert rates['time'][-1] == START + H              # última cerrada: la anterior a la vela en curso
    assert sim.symbol_info_tick("EURUSD").bid == 1.102  # tick = apertura de la vela en curso


def test_buy_at_ask_and_stop_loss(make_sim):
    sim = make_sim({"EURUSD": make_bars(flat(1.10000, 2) + [(1.10000, 1.10010, 1.09800, 1.09900)] + flat(1.099, 2))})
    sim.step()
    result = buy(sim, sl=1.09900)

    assert result.retcode == sim.TRADE_RETCODE_DONE and result.price == 1.10010   # ask = bid + 10 puntos
    sim.step()   # la vela 1 cierra sin tocar el SL
    sim.step()   # la vela 2 baja hasta 1.098: SL
    sim.step()

    assert sim.positions == {}
    exit_deal = sim.deals[-1]
    assert exit_deal.reason == sim.DEAL_REASON_SL and exit_deal.price == 1.09900
    assert sim.balance == pytest.approx(10000 - 11.0)    # (1.099 - 1.1001) * 0.1 * 100000


def test_gap_through_stop_loss_fills_at_open(make_sim):
    sim = make_sim({"EURUSD": make_bars(flat(1.10, 2) + [(1.09500, 1.09600, 1.09400, 1.09500)] + flat(1.095, 2))}, spread_points=0)
    sim.step()
    buy(sim, sl=1.09900)
    sim.step()
    sim.step()
    sim.step()
    assert sim.deals[-1].price == 1.09500   # peor que el SL: la vela abrió por debajo


def test_sl_and_tp_in_same_bar_assumes_sl(make_sim):
    sim = make_sim({"EURUSD": make_bars(flat(1.10, 2) + [(1.10, 1.11, 1.09, 1.10)] + flat(1.10, 2))}, spread_points=0)
    sim.step()
    buy(sim, sl=1.095, tp=1.105)
    for _ in range(3):
        sim.step()
    assert sim.deals[-1].reason == sim.DEAL_REASON_SL


def test_invalid_stops_and_volume_are_rejected(make_sim):
    sim = make_sim({"EURUSD": make_bars(flat(1.10, 5))})
    sim.step()
    assert buy(sim, sl=1.2).retcode == sim.TRADE_RETCODE_INVALID_STOPS
    assert buy(sim, volume=0.015).retcode == sim.TRADE_RETCODE_INVALID_VOLUME
    assert sim.positions == {}


def test_buy_limit_fills_when_price_reaches_it(make_sim):
    sim = make_sim({"EURUSD": make_bars(flat(1.10, 2) + [(1.10, 1.10, 1.0950, 1.0990)] + flat(1.099, 2))}, spread_points=0)
    sim.step()
    request = {"action": sim.TRADE_ACTION_PENDING, "symbol": "EURUSD", "volume": 0.1, "type": sim.ORDER_TYPE_BUY_LIMIT,
               "price": 1.0970, "sl": 0.0, "tp": 0.0, "magic": MAGIC}
    assert sim.order_send({**request, "price": 1.1050}).retcode == sim.TRADE_RETCODE_INVALID_PRICE   # un límite de compra va por debajo
    assert sim.order_send(request).retcode == sim.TRADE_RETCODE_DONE

    sim.step()
    assert len(sim.orders) == 1 and sim.positions == {}
    sim.step()
    sim.step()
    assert sim.orders == {} and list(sim.positions.values())[0]["price_open"] == 1.0970


def test_market_closed_when_symbol_has_no_bar(make_sim):
    bars_eur = make_bars(flat(1.10, 5))
    bars_gbp = make_bars(flat(1.30, 5)).drop(index=2)   # GBPUSD sin la vela de las 02:00
    sim = make_sim({"EURUSD": bars_eur, "GBPUSD": bars_gbp})
    for _ in range(3):
        sim.step()
    assert buy(sim, symbol="GBPUSD").retcode == sim.TRADE_RETCODE_MARKET_CLOSED
    assert buy(sim, symbol="EURUSD").retcode == sim.TRADE_RETCODE_DONE


def test_profit_converted_to_account_currency(make_sim):
    sim = make_sim({"USDJPY": make_bars(flat(150.000, 2) + flat(151.500, 3))}, spread_points=0)
    sim.step()
    buy(sim, symbol="USDJPY")
    sim.step()
    sim.step()
    sim.close_all_positions()
    # 1.5 JPY * 0.1 lotes * 100000 = 15000 JPY / 151.5 = 99.01 USD
    assert sim.deals[-1].profit == pytest.approx(99.01)


def test_missing_conversion_pair_raises():
    with pytest.raises(ValueError, match="hace falta cargar"):
        SimulatedMT5(symbols=["EURGBP"], timeframe="1h", bars={"EURGBP": make_bars(flat(0.85, 5))}, specs={})


def test_activate_and_deactivate_restore_mt5(make_sim):
    import portfolio.portfolio as portfolio_module
    original = portfolio_module.mt5
    sim = make_sim({"EURUSD": make_bars(flat(1.10, 5))})
    assert portfolio_module.mt5 is sim
    sim.deactivate()
    assert portfolio_module.mt5 is original


# ---------------- Backtest completo con los módulos del Framework
def build_framework(sim, tmp_path):
    from data_provider.data_provider import DataProvider
    from trading_director.trading_director import TradingDirector
    from signal_generator.signal_generator import SignalGenerator
    from signal_generator.properties.signal_generator_properties import RSIProps
    from position_sizer.position_sizer import PositionSizer
    from position_sizer.properties.position_sizer_properties import FixedSizingProps
    from portfolio.portfolio import Portfolio
    from risk_manager.risk_manager import RiskManager
    from risk_manager.properties.risk_manager_properties import MaxLeverageFactorRiskProps, TradingLimitsProps
    from order_executor.order_executor import OrderExecutor
    from notifications.notifications import NotificationService, TelegramNotificationProperties
    from trade_journal.trade_journal import TradeJournal

    symbols = list(sim.rates)
    events = queue.Queue()
    data_provider = DataProvider(events, symbols, "1h")
    portfolio = Portfolio(MAGIC)
    order_executor = OrderExecutor(events, portfolio)
    director = TradingDirector(
        events, data_provider,
        SignalGenerator(events, data_provider, portfolio, order_executor,
                        RSIProps(timeframe="1h", rsi_period=5, rsi_upper=70, rsi_lower=30, sl_points=300, tp_points=600)),
        PositionSizer(events, data_provider, FixedSizingProps(volume=0.1)),
        RiskManager(events, data_provider, portfolio, MaxLeverageFactorRiskProps(max_leverage_factor=5), TradingLimitsProps()),
        order_executor,
        NotificationService(TelegramNotificationProperties(token="", chat_id="")))
    journal = TradeJournal(MAGIC, "RSI", file_path=str(tmp_path / "bt_trades.csv"))
    return director, journal


def random_walk(n: int, seed: int, start: float, vol: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(0, vol, n))
    open_ = np.r_[start, close[:-1]]
    noise = np.abs(rng.normal(0, vol / 2, n))
    return make_bars(list(zip(open_.round(5), (np.maximum(open_, close) + noise).round(5),
                              (np.minimum(open_, close) - noise).round(5), close.round(5))))


def test_full_backtest_uses_framework_modules(make_sim, tmp_path):
    sim = make_sim({"EURUSD": random_walk(400, 1, 1.10, 0.0015), "GBPUSD": random_walk(400, 2, 1.30, 0.0015)})
    director, journal = build_framework(sim, tmp_path)

    stats = Backtester(sim, director, journal, results_dir=str(tmp_path)).run()

    trades = journal.get_trades()
    assert stats["trades"] == len(trades) > 10
    assert {t["exit_reason"] for t in trades} >= {"SL", "TP"}
    # El balance final cuadra con la suma de las operaciones y no queda nada abierto
    assert sim.balance == pytest.approx(10000 + sum(float(t["net_profit"]) for t in trades), abs=0.01)
    assert sim.positions == {} and sim.orders == {}
    assert (tmp_path / "bt_equity.csv").exists()


def test_compute_stats():
    trades = [{"symbol": "EURUSD", "net_profit": "30"}, {"symbol": "EURUSD", "net_profit": "-10"}, {"symbol": "GBPUSD", "net_profit": "-10"}]
    equity = [(0, 1000, 1000), (1, 1000, 1050), (2, 1000, 1010), (3, 1010, 1010)]
    stats = Backtester.compute_stats(trades, equity, 1000)

    assert stats["trades"] == 3 and stats["wins"] == 1
    assert stats["profit_factor"] == pytest.approx(1.5)
    assert stats["max_drawdown"] == 40 and stats["max_drawdown_pct"] == pytest.approx(40 / 1050)
    assert stats["by_symbol"]["GBPUSD"] == {"trades": 1, "net_profit": -10.0}
