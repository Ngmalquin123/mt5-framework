import MetaTrader5 as mt5
from conftest import drain

from events.events import OrderEvent, ExecutionEvent
from order_executor.order_executor import OrderExecutor
from portfolio.portfolio import Portfolio

MAGIC = 12345


def make_order(signal="BUY", target_order="MARKET", target_price=0.0, sl=0.0, tp=0.0) -> OrderEvent:
    return OrderEvent(symbol="EURUSD", signal=signal, target_order=target_order, target_price=target_price,
                      magic_number=MAGIC, sl=sl, tp=tp, volume=0.05)


def test_buy_at_ask_sell_at_bid(events):
    executor = OrderExecutor(events, Portfolio(MAGIC))

    executor.execute_order(make_order("BUY"))
    executor.execute_order(make_order("SELL"))

    buy, sell = mt5.STATE["requests"]
    assert buy["type"] == mt5.ORDER_TYPE_BUY and buy["price"] == 1.1201
    assert sell["type"] == mt5.ORDER_TYPE_SELL and sell["price"] == 1.1199
    assert [type(e) for e in drain(events)] == [ExecutionEvent, ExecutionEvent]


def test_filling_mode_supported_by_broker(events):
    mt5.STATE["filling_mode"] = 2   # el broker solo admite IOC
    OrderExecutor(events, Portfolio(MAGIC)).execute_order(make_order())
    assert mt5.STATE["requests"][-1]["type_filling"] == mt5.ORDER_FILLING_IOC


def test_sl_tp_rounded_to_symbol_digits(events):
    OrderExecutor(events, Portfolio(MAGIC)).execute_order(make_order("SELL", sl=1.1203999999999998, tp=1.1189000000000001))
    request = mt5.STATE["requests"][-1]
    assert request["sl"] == 1.1204 and request["tp"] == 1.1189


def test_sl_closer_than_stops_level_is_not_sent(events):
    mt5.STATE["stops_level"] = 100   # 100 puntos = 0.001
    OrderExecutor(events, Portfolio(MAGIC)).execute_order(make_order("SELL", sl=1.1204))
    assert mt5.STATE["requests"] == []


def test_order_send_returning_none_does_not_crash(events):
    mt5.STATE["send_returns_none"] = True
    OrderExecutor(events, Portfolio(MAGIC)).execute_order(make_order())
    assert drain(events) == []


def test_close_unknown_ticket_does_not_crash(events):
    executor = OrderExecutor(events, Portfolio(MAGIC))
    executor.close_position_by_ticket(999)
    executor.cancel_pending_order_by_ticket(999)
    assert mt5.STATE["requests"] == []


def test_close_sell_position_buys_at_ask(events):
    mt5.STATE["positions"] = [mt5.Position(5, MAGIC, mt5.POSITION_TYPE_SELL, "EURUSD", 0.05, 0.0, 0.0)]
    OrderExecutor(events, Portfolio(MAGIC)).close_position_by_ticket(5)

    request = mt5.STATE["requests"][-1]
    assert request["type"] == mt5.ORDER_TYPE_BUY and request["price"] == 1.1201
    assert mt5.STATE["positions"] == []


def test_expired_pending_orders_are_cancelled(events):
    now = mt5.STATE["time"]
    mt5.STATE["orders"] = [
        mt5.Order(1, MAGIC, "EURUSD", now - 61 * 60, 0.05),   # caducada (61 min)
        mt5.Order(2, MAGIC, "EURUSD", now - 10 * 60, 0.05),   # reciente
        mt5.Order(3, 999, "EURUSD", now - 61 * 60, 0.05),     # de otra estrategia
    ]
    OrderExecutor(events, Portfolio(MAGIC), pending_order_max_age_minutes=60).cancel_expired_pending_orders()

    assert [o.ticket for o in mt5.STATE["orders"]] == [2, 3]


def test_pending_orders_never_expire_by_default(events):
    mt5.STATE["orders"] = [mt5.Order(1, MAGIC, "EURUSD", 0, 0.05)]
    OrderExecutor(events, Portfolio(MAGIC)).cancel_expired_pending_orders()
    assert len(mt5.STATE["orders"]) == 1
