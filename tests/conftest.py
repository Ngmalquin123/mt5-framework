import os
import queue
import pytest

import MetaTrader5 as mt5

# Los tests usan siempre el MetaTrader5 simulado de tests/fakes (configurado en pyproject.toml)
assert getattr(mt5, "STATE", None) is not None, "Se está usando el MetaTrader5 REAL en los tests"


@pytest.fixture(autouse=True)
def fake_mt5(monkeypatch):
    """Cada test empieza con el MT5 simulado en su estado inicial y sin esperas reales."""
    mt5.reset()
    os.environ.update(MT5_PATH="x", MT5_LOGIN="1", MT5_PASSWORD="x", MT5_SERVER="x", MT5_TIMEOUT="1000", MT5_PORTABLE="False")

    # Las esperas (time.sleep) se anulan para que los tests sean instantáneos
    import order_executor.order_executor as order_executor_module
    import trading_director.trading_director as trading_director_module
    monkeypatch.setattr(order_executor_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(trading_director_module.time, "sleep", lambda seconds: None)
    return mt5.STATE


@pytest.fixture
def events():
    return queue.Queue()


def drain(events_queue: queue.Queue) -> list:
    """Saca todos los eventos de la cola."""
    items = []
    while not events_queue.empty():
        items.append(events_queue.get())
    return items
