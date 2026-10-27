import queue
import MetaTrader5 as mt5

from platform_connector.platform_connector import PlatformConnector
from trading_director.trading_director import TradingDirector


class FakeNotifications():
    def __init__(self):
        self.titles = []

    def send_notification(self, title: str, message: str):
        self.titles.append(title)


class FakeOrderExecutor():
    def cancel_expired_pending_orders(self):
        pass


class ScriptedDataProvider():
    """Ejecuta una acción por vuelta del bucle: "ok", "error", "ctrl+c". Al terminar el guion para el bucle."""

    def __init__(self, actions: list):
        self.actions = list(actions)
        self.director = None
        self.symbols = ["EURUSD"]

    def check_for_new_data(self):
        action = self.actions.pop(0) if self.actions else "stop"
        if action == "error":
            raise ValueError("fallo simulado")
        if action == "ctrl+c":
            raise KeyboardInterrupt
        if action == "stop":
            self.director.continue_trading = False


class FakeTradeJournal():
    def __init__(self):
        self.new_trades = []
        self.summary_days = []

    def update(self):
        trades, self.new_trades = self.new_trades, []
        return trades

    def daily_summary(self, day):
        self.summary_days.append(day)
        return f"Resumen del {day}"


def make_director(actions: list, platform_connector=None, trade_journal=None):
    data_provider = ScriptedDataProvider(actions)
    notifications = FakeNotifications()
    director = TradingDirector(queue.Queue(), data_provider, None, None, None, FakeOrderExecutor(), notifications,
                               platform_connector=platform_connector, trade_journal=trade_journal)
    data_provider.director = director
    return director, notifications


def test_errors_do_not_stop_the_framework():
    director, notifications = make_director(["error", "error", "error", "ok", "ok"])
    director.execute()

    # Solo se notifica el primer error de la racha y el contador se reinicia al volver a funcionar
    assert notifications.titles == ["ERROR EN EL FRAMEWORK"]
    assert director.consecutive_errors == 0


def test_too_many_consecutive_errors_stop_the_framework():
    director, notifications = make_director(["error"] * 100)
    director.execute()

    assert director.consecutive_errors == director.max_consecutive_errors
    assert notifications.titles == ["ERROR EN EL FRAMEWORK", "FRAMEWORK DETENIDO"]


def test_ctrl_c_stops_cleanly():
    director, notifications = make_director(["ok", "ctrl+c"])
    director.execute()

    assert notifications.titles == ["FRAMEWORK DETENIDO"]
    assert mt5.STATE["shutdowns"] == 1


def test_reconnection_notifications():
    director, notifications = make_director([], platform_connector=PlatformConnector(["EURUSD"]))
    director.connection_check_seconds = 0

    mt5.STATE["connected"] = False
    mt5.STATE["reconnect_ok"] = False
    assert director._check_platform_connection() is False

    mt5.STATE["reconnect_ok"] = True
    assert director._check_platform_connection() is True
    assert notifications.titles == ["CONEXIÓN PERDIDA", "CONEXIÓN RESTABLECIDA"]


def test_closed_trades_are_notified():
    journal = FakeTradeJournal()
    director, notifications = make_director([], trade_journal=journal)
    director.trade_journal_check_seconds = 0

    # Al arrancar: operaciones antiguas, se guardan sin avisar
    journal.new_trades = [{"side": "SELL", "volume": 0.05, "symbol": "GBPUSD"}]
    director._run_periodic_tasks()
    assert notifications.titles == []

    journal.new_trades = [{"side": "BUY", "volume": 0.05, "symbol": "EURUSD", "exit_reason": "TP",
                           "entry_price": 1.1, "exit_price": 1.101, "net_profit": 5.0}]
    director._run_periodic_tasks()
    assert notifications.titles == ["EURUSD - TRADE CERRADO"]


def test_daily_summary_sent_when_server_day_changes():
    from datetime import date
    journal = FakeTradeJournal()
    director, notifications = make_director([], trade_journal=journal)

    mt5.STATE["time"] = 1_761_609_000          # 27/10/2025 23:50 (hora servidor)
    director._check_daily_summary()
    director._check_daily_summary()
    assert notifications.titles == []           # mismo día: nada

    mt5.STATE["time"] += 15 * 60                # 28/10/2025 00:05
    director._check_daily_summary()
    assert notifications.titles == ["RESUMEN DIARIO"]
    assert journal.summary_days == [date(2025, 10, 27)]
