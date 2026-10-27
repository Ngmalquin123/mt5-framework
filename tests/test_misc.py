import re
import sys
import pytest
import MetaTrader5 as mt5
import telegram
from conftest import drain

from data_provider.data_provider import DataProvider
from notifications.notifications import NotificationService, TelegramNotificationProperties
from platform_connector.platform_connector import PlatformConnector
from utils.logger import TeeLogger
from utils.utils import Utils


# ---------------- Data Provider
def test_first_read_is_only_a_reference(events):
    data_provider = DataProvider(events, ["EURUSD"], "1min")

    data_provider.check_for_new_data()
    assert drain(events) == []      # posible vela antigua al arrancar: no genera evento

    mt5.STATE["time"] += 60
    data_provider.check_for_new_data()
    assert len(drain(events)) == 1

    data_provider.check_for_new_data()
    assert drain(events) == []      # misma vela: no se repite


def test_invalid_timeframe_raises(events):
    with pytest.raises(ValueError):
        DataProvider(events, ["EURUSD"], "7min")._map_timeframes("7min")


# ---------------- Platform Connector
def test_portable_is_parsed_without_eval():
    PlatformConnector(["EURUSD"])
    assert mt5.STATE["init_kwargs"]["portable"] is False


def test_algo_trading_disabled_raises():
    mt5.STATE["trade_allowed"] = False
    with pytest.raises(Exception, match="trading algorítmico"):
        PlatformConnector(["EURUSD"])


# ---------------- Notificaciones
def test_notifications_disabled_without_token():
    service = NotificationService(TelegramNotificationProperties(token="", chat_id=""))
    service.send_notification("titulo", "mensaje")   # no hace nada y no falla


def test_notification_is_sent():
    telegram.SENT_MESSAGES.clear()
    NotificationService(TelegramNotificationProperties(token="ok", chat_id="42")).send_notification("Titulo", "Mensaje")
    assert telegram.SENT_MESSAGES == [("42", "Titulo\nMensaje")]


def test_telegram_failure_does_not_stop_trading():
    NotificationService(TelegramNotificationProperties(token="fail", chat_id="42")).send_notification("t", "m")


# ---------------- Utils y logger
def test_dateprint_format():
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\.\d{3}", Utils.dateprint())


def test_currency_conversion_without_pair_returns_zero():
    assert Utils.convert_currency_amount_to_another_currency(10, "XAU", "BTC") == 0.0


def test_logger_writes_daily_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    TeeLogger.start(str(tmp_path))
    print("hola log")

    log_files = list(tmp_path.glob("trading_*.log"))
    assert len(log_files) == 1 and "hola log" in log_files[0].read_text(encoding="utf-8")
