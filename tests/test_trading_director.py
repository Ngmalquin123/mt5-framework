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

    def check_for_new_data(self):
        action = self.actions.pop(0) if self.actions else "stop"
        if action == "error":
            raise ValueError("fallo simulado")
        if action == "ctrl+c":
            raise KeyboardInterrupt
        if action == "stop":
            self.director.continue_trading = False


def make_director(actions: list, platform_connector=None):
    data_provider = ScriptedDataProvider(actions)
    notifications = FakeNotifications()
    director = TradingDirector(queue.Queue(), data_provider, None, None, None, FakeOrderExecutor(), notifications,
                               platform_connector=platform_connector)
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
