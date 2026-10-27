"""
MetaTrader5 simulado para los tests.

Imita la parte de la API de MT5 que usa el Framework y guarda todo en el diccionario STATE,
que los tests pueden modificar (precios, posiciones, conexión...) e inspeccionar (órdenes enviadas).
Así los tests funcionan en cualquier sistema y NUNCA tocan una cuenta real.
"""
from collections import namedtuple
import numpy as np

# ---------------- Constantes
_TIMEFRAMES = "M1 M2 M3 M4 M5 M6 M10 M12 M15 M20 M30 H1 H2 H3 H4 H6 H8 H12 D1 W1 MN1".split()
for _i, _name in enumerate(_TIMEFRAMES):
    globals()["TIMEFRAME_" + _name] = _i

ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 2, 3, 4, 5
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_REMOVE = 1, 5, 8
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL, TRADE_RETCODE_REJECT = 10009, 10010, 10006
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2

# ---------------- Estructuras
SymbolInfo = namedtuple("SymbolInfo", "name visible digits point spread trade_stops_level volume_min volume_max volume_step "
                                      "trade_tick_size trade_contract_size currency_profit bid ask filling_mode")
Tick = namedtuple("Tick", "time bid ask")
AccountInfo = namedtuple("AccountInfo", "login name company server leverage currency balance equity trade_mode")
TerminalInfo = namedtuple("TerminalInfo", "trade_allowed connected")
Position = namedtuple("Position", "ticket magic type symbol volume profit swap")
Order = namedtuple("Order", "ticket magic symbol time_setup volume_initial")
Deal = namedtuple("Deal", "time_msc magic profit commission swap")
TradeResult = namedtuple("TradeResult", "retcode comment order price request")


class TradeRequest():
    def __init__(self, request: dict):
        self.__dict__.update(request)


STATE: dict = {}


def reset() -> None:
    """Vuelve al estado inicial: precios subiendo, sin posiciones, conectado y sin órdenes enviadas."""
    STATE.clear()
    STATE.update({
        "time": 1_700_000_040,                          # hora del servidor (segundos), múltiplo de 60
        "closes": list(np.linspace(1.10, 1.12, 40)),    # cierres de las velas (la última es la más reciente)
        "bid": 1.1199, "ask": 1.1201,
        "spread": 2, "stops_level": 0, "filling_mode": 2,
        "balance": 10000.0, "equity": 10000.0, "trade_mode": ACCOUNT_TRADE_MODE_DEMO,
        "connected": True, "reconnect_ok": True, "trade_allowed": True,
        "positions": [], "orders": [], "deals": [],
        "requests": [],                                 # todas las peticiones enviadas con order_send
        "send_returns_none": False, "send_retcode": TRADE_RETCODE_DONE,
        "init_kwargs": None, "shutdowns": 0, "next_ticket": 1000,
    })


reset()


# ---------------- Plataforma
def initialize(**kwargs) -> bool:
    STATE["init_kwargs"] = kwargs
    if STATE["reconnect_ok"]:
        STATE["connected"] = True
    return STATE["reconnect_ok"]


def shutdown() -> None:
    STATE["shutdowns"] += 1


def last_error():
    return (1, "Success")


def terminal_info():
    return TerminalInfo(STATE["trade_allowed"], STATE["connected"])


def account_info():
    return AccountInfo(1, "Test", "Broker", "Server", 100, "USD", STATE["balance"], STATE["equity"], STATE["trade_mode"])


# ---------------- Mercado
def symbol_info(symbol: str):
    return SymbolInfo(symbol, True, 5, 0.00001, STATE["spread"], STATE["stops_level"], 0.01, 100.0, 0.01,
                      0.00001, 100000, "USD", STATE["bid"], STATE["ask"], STATE["filling_mode"])


def symbol_select(symbol: str, enable: bool) -> bool:
    return True


def symbol_info_tick(symbol: str):
    return Tick(STATE["time"], STATE["bid"], STATE["ask"])


def copy_rates_from_pos(symbol: str, timeframe: int, start_pos: int, count: int):
    closes = STATE["closes"][-count:]
    dtype = np.dtype([('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'), ('close', 'f8'),
                      ('tick_volume', 'i8'), ('spread', 'i4'), ('real_volume', 'i8')])
    # La última vela cerrada empieza un minuto antes de la hora actual
    return np.array([(STATE["time"] - 60 * (len(closes) - i), c, c, c, c, 1, 1, 0) for i, c in enumerate(closes)], dtype=dtype)


# ---------------- Posiciones, órdenes y deals
def positions_get(symbol: str = None, ticket: int = None):
    return tuple(p for p in STATE["positions"]
                 if (symbol is None or p.symbol == symbol) and (ticket is None or p.ticket == ticket))


def orders_get(ticket: int = None):
    return tuple(o for o in STATE["orders"] if ticket is None or o.ticket == ticket)


def history_deals_get(*args, **kwargs):
    if "position" in kwargs:
        return (Deal(STATE["time"] * 1000, 0, 0.0, 0.0, 0.0),)
    return tuple(STATE["deals"])


def order_send(request: dict):
    STATE["requests"].append(request)
    if STATE["send_returns_none"]:
        return None

    retcode = STATE["send_retcode"]
    if retcode == TRADE_RETCODE_DONE:
        if request["action"] == TRADE_ACTION_DEAL and "position" in request:
            # Cierre de una posición
            STATE["positions"] = [p for p in STATE["positions"] if p.ticket != request["position"]]
        elif request["action"] == TRADE_ACTION_REMOVE:
            STATE["orders"] = [o for o in STATE["orders"] if o.ticket != request["order"]]

    STATE["next_ticket"] += 1
    return TradeResult(retcode, "done" if retcode == TRADE_RETCODE_DONE else "rejected",
                       STATE["next_ticket"], request.get("price", 0.0), TradeRequest(request))
