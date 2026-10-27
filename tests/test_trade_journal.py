from datetime import date
import pandas as pd
import pytest

from backtest.simulated_mt5 import SimulatedMT5
from trade_journal.trade_journal import TradeJournal

H = 3600
START = 1_735_689_600   # 2025-01-01 00:00 UTC
MAGIC = 12345


@pytest.fixture
def sim():
    """Broker simulado con dos operaciones cerradas: una ganadora y una perdedora, y otra de otra estrategia."""
    prices = [1.10, 1.10, 1.11, 1.11, 1.10, 1.10, 1.10]
    bars = pd.DataFrame([{"time": START + i * H, "open": p, "high": p, "low": p, "close": p, "spread": 0} for i, p in enumerate(prices)])
    sim = SimulatedMT5(symbols=["EURUSD"], timeframe="1h", bars={"EURUSD": bars}, specs={}, commission_per_lot=3.0, spread_points=0)
    sim.activate()

    def trade(magic, side):
        sim.order_send({"action": sim.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1, "type": side, "magic": magic})

    def close_all_of(magic):
        for ticket, position in list(sim.positions.items()):
            if position["magic"] == magic:
                sim.order_send({"action": sim.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1, "position": ticket})

    sim.step()
    trade(MAGIC, sim.ORDER_TYPE_BUY)
    trade(999, sim.ORDER_TYPE_BUY)
    sim.step()
    sim.step()                      # 1.11: compra ganadora
    close_all_of(MAGIC)
    close_all_of(999)
    trade(MAGIC, sim.ORDER_TYPE_BUY)
    sim.step()
    sim.step()                      # 1.10: compra perdedora
    close_all_of(MAGIC)
    yield sim
    sim.deactivate()


def test_records_closed_trades_of_the_strategy(sim, tmp_path):
    journal = TradeJournal(MAGIC, "RSI", file_path=str(tmp_path / "trades.csv"))
    new_trades = journal.update(0, 2**40)

    assert len(new_trades) == 2
    win = journal.get_trades()[0]
    assert win["side"] == "BUY" and win["exit_reason"] == "FRAMEWORK" and win["entry_time"] == "2025-01-01 00:00:00"
    assert float(win["entry_price"]) == 1.10 and float(win["exit_price"]) == 1.11
    # 0.01 * 0.1 lotes * 100000 = 100 de beneficio - 0.30 de comisión en la entrada y 0.30 en la salida
    assert float(win["profit"]) == 100.0 and float(win["net_profit"]) == 99.4


def test_trades_are_not_repeated_after_restart(sim, tmp_path):
    path = str(tmp_path / "trades.csv")
    TradeJournal(MAGIC, "RSI", file_path=path).update(0, 2**40)

    restarted = TradeJournal(MAGIC, "RSI", file_path=path)
    assert restarted.update(0, 2**40) == []
    assert len(restarted.get_trades()) == 2


def test_daily_summary(sim, tmp_path):
    journal = TradeJournal(MAGIC, "RSI", file_path=str(tmp_path / "trades.csv"))
    journal.update(0, 2**40)

    summary = journal.daily_summary(date(2025, 1, 1))
    assert "Operaciones: 2 (1 ganadoras, 1 perdedoras)" in summary
    assert "Resultado: -1.20 USD" in summary    # +99.40 - 100.60
    assert "Sin operaciones" in journal.daily_summary(date(2025, 1, 2))
