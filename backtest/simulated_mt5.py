"""
MetaTrader5 simulado para el backtest.

Imita la parte de la API de MT5 que usa el Framework (velas, ticks, posiciones, órdenes, deals, cuenta...)
sobre velas históricas guardadas en CSV. Al activarlo, todos los módulos del Framework (Portfolio,
OrderExecutor, RiskManager, señales...) lo usan en lugar del MT5 real SIN cambiar su código.

Supuestos de la simulación:
    - El precio del tick es la apertura de la vela en curso (bid). El ask es bid + spread de la vela.
    - SL, TP y órdenes pendientes se comprueban con el máximo y el mínimo de cada vela al cerrarse.
      Si en una misma vela se tocan SL y TP se asume el SL (lo más conservador).
      Si la vela abre ya más allá del nivel (gap) se ejecuta al precio de apertura.
    - No se simulan swaps ni margen. La comisión es fija por lote y por lado (entrada y salida).
"""
from collections import namedtuple
from datetime import datetime, timezone
from types import SimpleNamespace
import bisect
import json
import os
import sys
import types
import numpy as np
import pandas as pd

# ---------------- Estructuras (mismos nombres de campos que el MT5 real)
Tick = namedtuple("Tick", "time bid ask last volume time_msc")
SymbolInfo = namedtuple("SymbolInfo", "name visible select digits point spread trade_stops_level volume_min volume_max volume_step "
                                      "trade_tick_size trade_contract_size currency_base currency_profit bid ask filling_mode")
AccountInfo = namedtuple("AccountInfo", "login trade_mode leverage balance credit profit equity margin margin_free currency server name company")
TerminalInfo = namedtuple("TerminalInfo", "connected trade_allowed name path")
Position = namedtuple("Position", "ticket time time_msc type magic identifier volume price_open sl tp price_current swap profit symbol comment")
Order = namedtuple("Order", "ticket time_setup type magic volume_initial volume_current price_open sl tp symbol comment")
Deal = namedtuple("Deal", "ticket order time time_msc type entry magic position_id reason volume price commission swap profit fee symbol comment")
TradeResult = namedtuple("TradeResult", "retcode deal order volume price bid ask comment request")

# Comentario de los cierres forzados al terminar el backtest (el diario los muestra como "FIN BACKTEST")
BACKTEST_END_COMMENT = "FIN BACKTEST"

RATES_DTYPE = np.dtype([('time', 'i8'), ('open', 'f8'), ('high', 'f8'), ('low', 'f8'), ('close', 'f8'),
                        ('tick_volume', 'i8'), ('spread', 'i4'), ('real_volume', 'i8')])

# Especificaciones que se guardan del símbolo al descargar los datos (download_data.py)
SYMBOL_SPEC_FIELDS = ("digits", "point", "trade_stops_level", "volume_min", "volume_max", "volume_step",
                      "trade_tick_size", "trade_contract_size", "currency_base", "currency_profit", "spread")


class SimulatedMT5():

    # ---------------- Constantes (mismos valores que el MT5 real)
    TIMEFRAME_M1, TIMEFRAME_M2, TIMEFRAME_M3, TIMEFRAME_M4, TIMEFRAME_M5, TIMEFRAME_M6 = 1, 2, 3, 4, 5, 6
    TIMEFRAME_M10, TIMEFRAME_M12, TIMEFRAME_M15, TIMEFRAME_M20, TIMEFRAME_M30 = 10, 12, 15, 20, 30
    TIMEFRAME_H1, TIMEFRAME_H2, TIMEFRAME_H3, TIMEFRAME_H4, TIMEFRAME_H6 = 16385, 16386, 16387, 16388, 16390
    TIMEFRAME_H8, TIMEFRAME_H12, TIMEFRAME_D1, TIMEFRAME_W1, TIMEFRAME_MN1 = 16392, 16396, 16408, 32769, 49153

    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 2, 3, 4, 5
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY = 0, 1, 2, 3
    DEAL_REASON_CLIENT, DEAL_REASON_EXPERT, DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO = 0, 3, 4, 5, 6
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_MODIFY, TRADE_ACTION_REMOVE = 1, 5, 6, 7, 8
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_REJECT, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10006, 10009, 10010
    TRADE_RETCODE_INVALID, TRADE_RETCODE_INVALID_VOLUME, TRADE_RETCODE_INVALID_PRICE = 10013, 10014, 10015
    TRADE_RETCODE_INVALID_STOPS, TRADE_RETCODE_MARKET_CLOSED = 10016, 10018
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2

    _TIMEFRAME_SECONDS = {'1min': 60, '2min': 120, '3min': 180, '4min': 240, '5min': 300, '6min': 360, '10min': 600, '12min': 720,
                          '15min': 900, '20min': 1200, '30min': 1800, '1h': 3600, '2h': 7200, '3h': 10800, '4h': 14400,
                          '6h': 21600, '8h': 28800, '12h': 43200, '1d': 86400, '1w': 604800, '1M': 2419200}
    _TIMEFRAME_CODES = {'1min': 1, '2min': 2, '3min': 3, '4min': 4, '5min': 5, '6min': 6, '10min': 10, '12min': 12,
                        '15min': 15, '20min': 20, '30min': 30, '1h': 16385, '2h': 16386, '3h': 16387, '4h': 16388,
                        '6h': 16390, '8h': 16392, '12h': 16396, '1d': 16408, '1w': 32769, '1M': 49153}

    def __init__(self, symbols: list, timeframe: str, start: str = None, end: str = None, data_dir: str = "backtest/data",
                initial_balance: float = 10000.0, account_currency: str = "USD", leverage: int = 100,
                commission_per_lot: float = 0.0, spread_points: int = None, bars: dict = None, specs: dict = None):
        """
        Initializes the simulated broker.

        Args:
            symbols (list): Symbols to load. Include the pairs needed to convert profits to the account currency.
            timeframe (str): Timeframe of the data ('1min', '1h'...). It must be the same one the strategy uses.
            start (str, optional): First date to simulate ("YYYY-MM-DD"). Older bars are only used as history.
            end (str, optional): Last date to simulate (not included).
            data_dir (str): Folder with the CSV files created by download_data.py.
            initial_balance (float): Initial balance of the account.
            account_currency (str): Currency of the account.
            leverage (int): Leverage of the account (only informative).
            commission_per_lot (float): Commission per lot and side, in account currency.
            spread_points (int, optional): Fixed spread in points. By default the spread of each bar is used.
            bars (dict, optional): {symbol: DataFrame} to use instead of the CSV files (tests).
            specs (dict, optional): {symbol: dict} with the symbol specifications instead of the JSON files.
        """
        if timeframe not in self._TIMEFRAME_SECONDS:
            raise ValueError(f"Timeframe {timeframe} no es válido.")

        self.timeframe = timeframe
        self.timeframe_code = self._TIMEFRAME_CODES[timeframe]
        self.timeframe_seconds = self._TIMEFRAME_SECONDS[timeframe]
        self.account_currency = account_currency.upper()
        self.leverage = leverage
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.commission_per_lot = commission_per_lot
        self.spread_points = spread_points

        # Datos de cada símbolo: velas (array estructurado como el que devuelve MT5) y especificaciones
        self.rates: dict = {}
        self.times: dict = {}
        self.specs: dict = {}

        # Antes de cargar nada comprobamos que estén todos los CSV, para indicar de una vez todo lo que falta
        if bars is None:
            missing = [s for s in symbols if not os.path.exists(os.path.join(data_dir, f"{s}_{timeframe}.csv"))]
            if missing:
                raise FileNotFoundError(
                    f"\n\nFaltan los datos de {', '.join(missing)} en {timeframe} (carpeta {data_dir}).\n"
                    f"Descárgalos primero con MT5 abierto (cambia la fecha de inicio si quieres otro periodo):\n\n"
                    f"    uv run python -m backtest.download_data --symbols {' '.join(symbols)} --timeframe {timeframe} "
                    f"--start {start or '2025-01-01'}\n")

        for symbol in symbols:
            df = bars[symbol] if bars is not None else self._read_csv(data_dir, symbol, timeframe)
            self.rates[symbol] = self._to_rates(df)
            self.times[symbol] = self.rates[symbol]['time']
            spec = (specs or {}).get(symbol) if specs is not None else self._read_spec(data_dir, symbol)
            self.specs[symbol] = self._complete_spec(symbol, spec or {})

        for symbol in symbols:
            self._check_conversion_available(symbol)

        # Línea temporal: todas las aperturas de vela de cualquier símbolo dentro del periodo
        all_times = np.unique(np.concatenate(list(self.times.values())))
        start_ts = self._to_timestamp(start) if start else all_times[0]
        end_ts = self._to_timestamp(end) if end else all_times[-1] + 1
        self.timeline = all_times[(all_times >= start_ts) & (all_times < end_ts)]
        if len(self.timeline) == 0:
            raise ValueError(f"No hay velas entre {start} y {end} en los datos cargados")

        self.step_index: int = -1
        self.now: int = int(self.timeline[0])
        self._bar_count: dict = {}       # nº de velas con time <= now (la última es la vela "en curso")
        self._update_bar_counts()

        # Estado de la cuenta
        self.positions: dict = {}
        self.orders: dict = {}
        self.deals: list = []
        self._deal_times: list = []          # time de cada deal (en orden), para buscar por fechas con bisect
        self._deals_by_position: dict = {}
        self.next_ticket: int = 1
        self.equity_curve: list = []
        self._saved_modules: list = []

    # ---------------- Carga de datos
    @staticmethod
    def _read_csv(data_dir: str, symbol: str, timeframe: str) -> pd.DataFrame:
        path = os.path.join(data_dir, f"{symbol}_{timeframe}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No hay datos de {symbol} {timeframe} en {data_dir}. Descárgalos antes con: "
                                    f"uv run python -m backtest.download_data --symbols {symbol} --timeframe {timeframe} --start AAAA-MM-DD")
        return pd.read_csv(path)

    @staticmethod
    def _read_spec(data_dir: str, symbol: str) -> dict:
        path = os.path.join(data_dir, f"{symbol}.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _to_rates(df: pd.DataFrame) -> np.ndarray:
        df = df.copy()
        if not np.issubdtype(df['time'].dtype, np.number):
            df['time'] = pd.to_datetime(df['time']).astype('int64') // 10**9
        for column in ('tick_volume', 'spread', 'real_volume'):
            if column not in df:
                df[column] = 0
        df = df.sort_values('time').drop_duplicates('time')
        rates = np.empty(len(df), dtype=RATES_DTYPE)
        for column in RATES_DTYPE.names:
            rates[column] = df[column].to_numpy()
        return rates

    def _complete_spec(self, symbol: str, spec: dict) -> dict:
        # Valores típicos de un par de divisas si no se han descargado las especificaciones
        digits = spec.get("digits", 3 if "JPY" in symbol else 5)
        defaults = {"digits": digits, "point": 10 ** -digits, "trade_stops_level": 0, "volume_min": 0.01,
                    "volume_max": 100.0, "volume_step": 0.01, "trade_tick_size": 10 ** -digits,
                    "trade_contract_size": 100000.0, "currency_base": symbol[:3], "currency_profit": symbol[3:6], "spread": 10}
        return {**defaults, **spec}

    def _check_conversion_available(self, symbol: str) -> None:
        profit_ccy = self.specs[symbol]["currency_profit"]
        if profit_ccy != self.account_currency and self._find_conversion_symbol(profit_ccy) is None:
            raise ValueError(f"Para pasar los beneficios de {symbol} ({profit_ccy}) a {self.account_currency} hace falta cargar también "
                             f"los datos de un par con ambas divisas (p.ej. {profit_ccy}{self.account_currency} o {self.account_currency}{profit_ccy})")

    def _find_conversion_symbol(self, ccy: str):
        for candidate in self.rates:
            spec = self.specs[candidate]
            if {spec["currency_base"], spec["currency_profit"]} == {ccy, self.account_currency}:
                return candidate
        return None

    @staticmethod
    def _to_timestamp(value) -> int:
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, datetime):
            return int(value.replace(tzinfo=timezone.utc).timestamp()) if value.tzinfo is None else int(value.timestamp())
        return int(pd.Timestamp(value).tz_localize(None).value // 10**9)

    # ---------------- Activación: sustituye al MetaTrader5 real en todo el Framework
    def activate(self) -> None:
        """
        Makes every module of the Framework use this simulated MT5.
        Call it BEFORE importing the Framework modules (and it also patches the ones already imported).
        """
        self._saved_modules = [(None, sys.modules.get("MetaTrader5"))]
        sys.modules["MetaTrader5"] = self

        # Módulos ya importados que tienen "import MetaTrader5 as mt5"
        for module in list(sys.modules.values()):
            try:
                current = getattr(module, "mt5", None) if isinstance(module, types.ModuleType) else None
            except Exception:
                continue
            # El MT5 real (o el de los tests) o un MT5 simulado de un backtest anterior
            is_mt5 = getattr(current, "__name__", "") == "MetaTrader5" or isinstance(current, SimulatedMT5)
            if current is not None and current is not self and is_mt5:
                self._saved_modules.append((module, current))
                module.mt5 = self

    def deactivate(self) -> None:
        """Restores the original MetaTrader5 module."""
        for module, original in self._saved_modules:
            if module is None:
                if original is None:
                    sys.modules.pop("MetaTrader5", None)
                else:
                    sys.modules["MetaTrader5"] = original
            else:
                module.mt5 = original
        self._saved_modules = []

    # ---------------- Avance del tiempo
    def step(self) -> bool:
        """
        Moves to the next bar of the timeline. The bars that close are used to check SL, TP and pending orders.

        Returns:
            bool: False when there are no more bars.
        """
        if self.step_index + 1 >= len(self.timeline):
            return False

        previous_now = self.now
        self.step_index += 1
        self.now = int(self.timeline[self.step_index])
        self._update_bar_counts()

        if self.step_index > 0:
            for symbol in self.rates:
                # Velas que se han cerrado entre el paso anterior y este
                first = np.searchsorted(self.times[symbol], previous_now, side='left')
                last = np.searchsorted(self.times[symbol], self.now, side='left')
                for bar in self.rates[symbol][first:last]:
                    self._process_closed_bar(symbol, bar)

        return True

    def _update_bar_counts(self) -> None:
        for symbol, times in self.times.items():
            self._bar_count[symbol] = int(np.searchsorted(times, self.now, side='right'))

    def is_market_open(self, symbol: str) -> bool:
        count = self._bar_count.get(symbol, 0)
        return count > 0 and self.times[symbol][count - 1] == self.now

    def record_equity(self) -> None:
        self.equity_curve.append((self.now, self.balance, self._equity()))

    # ---------------- Precios
    def _current_bid(self, symbol: str) -> float:
        count = self._bar_count[symbol]
        bar = self.rates[symbol][count - 1]
        # Vela en curso: su apertura. Si el símbolo no cotiza ahora: el último cierre
        return float(bar['open']) if bar['time'] == self.now else float(bar['close'])

    def _spread_price(self, symbol: str, bar=None) -> float:
        spec = self.specs[symbol]
        if self.spread_points is not None:
            spread = self.spread_points
        else:
            if bar is None:
                bar = self.rates[symbol][self._bar_count[symbol] - 1]
            spread = int(bar['spread']) if bar['spread'] > 0 else spec["spread"]
        return spread * spec["point"]

    def _current_ask(self, symbol: str) -> float:
        return round(self._current_bid(symbol) + self._spread_price(symbol), self.specs[symbol]["digits"])

    def _to_account_currency(self, amount: float, ccy: str) -> float:
        if ccy == self.account_currency:
            return amount
        conversion_symbol = self._find_conversion_symbol(ccy)
        price = self._current_bid(conversion_symbol)
        # Si la divisa de la cuenta es la base (USDJPY con cuenta en USD) dividimos, si no multiplicamos
        return amount / price if self.specs[conversion_symbol]["currency_base"] == self.account_currency else amount * price

    def _profit(self, symbol: str, position_type: int, volume: float, price_open: float, price_close: float) -> float:
        direction = 1 if position_type == self.POSITION_TYPE_BUY else -1
        profit_ccy = (price_close - price_open) * direction * volume * self.specs[symbol]["trade_contract_size"]
        return round(self._to_account_currency(profit_ccy, self.specs[symbol]["currency_profit"]), 2)

    def _floating_profit(self, position: dict) -> float:
        close_price = self._current_bid(position["symbol"]) if position["type"] == self.POSITION_TYPE_BUY else self._current_ask(position["symbol"])
        return self._profit(position["symbol"], position["type"], position["volume"], position["price_open"], close_price)

    def _equity(self) -> float:
        return round(self.balance + sum(self._floating_profit(p) for p in self.positions.values()), 2)

    # ---------------- Plataforma y cuenta
    def initialize(self, *args, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self):
        return (1, "Success")

    def terminal_info(self):
        return TerminalInfo(True, True, "Backtest", "")

    def account_info(self):
        equity = self._equity()
        return AccountInfo(0, self.ACCOUNT_TRADE_MODE_DEMO, self.leverage, round(self.balance, 2), 0.0, round(equity - self.balance, 2),
                           equity, 0.0, equity, self.account_currency, "Backtest", "Backtest", "Backtest")

    # ---------------- Mercado
    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return symbol in self.rates

    def symbol_info(self, symbol: str):
        if symbol not in self.rates or self._bar_count[symbol] == 0:
            return None
        spec = self.specs[symbol]
        spread = round(self._spread_price(symbol) / spec["point"])
        return SymbolInfo(symbol, True, True, spec["digits"], spec["point"], spread, spec["trade_stops_level"], spec["volume_min"],
                          spec["volume_max"], spec["volume_step"], spec["trade_tick_size"], spec["trade_contract_size"],
                          spec["currency_base"], spec["currency_profit"], self._current_bid(symbol), self._current_ask(symbol), 1)

    def symbol_info_tick(self, symbol: str):
        if symbol not in self.rates or self._bar_count[symbol] == 0:
            return None
        bid = self._current_bid(symbol)
        return Tick(self.now, bid, self._current_ask(symbol), 0.0, 0, self.now * 1000)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        if symbol not in self.rates:
            return None
        if timeframe != self.timeframe_code:
            print(f"BACKTEST: solo hay datos de {self.timeframe}. La estrategia ha pedido otro timeframe para {symbol}")
            return None
        # La posición 0 es la vela en curso (como en MT5), la 1 la última cerrada...
        end = self._bar_count[symbol] - start_pos
        return self.rates[symbol][max(0, end - count):max(0, end)].copy()

    # ---------------- Posiciones, órdenes y deals
    def positions_get(self, symbol: str = None, ticket: int = None, **kwargs):
        result = []
        for p in self.positions.values():
            if (symbol is None or p["symbol"] == symbol) and (ticket is None or p["ticket"] == ticket):
                result.append(Position(p["ticket"], p["time"], p["time"] * 1000, p["type"], p["magic"], p["ticket"], p["volume"],
                                       p["price_open"], p["sl"], p["tp"], 0.0, 0.0, self._floating_profit(p), p["symbol"], p["comment"]))
        return tuple(result)

    def orders_get(self, symbol: str = None, ticket: int = None, **kwargs):
        return tuple(Order(o["ticket"], o["time_setup"], o["type"], o["magic"], o["volume"], o["volume"], o["price_open"], o["sl"],
                           o["tp"], o["symbol"], o["comment"])
                     for o in self.orders.values()
                     if (symbol is None or o["symbol"] == symbol) and (ticket is None or o["ticket"] == ticket))

    def history_deals_get(self, date_from=None, date_to=None, position: int = None, ticket: int = None, **kwargs):
        if position is not None:
            return tuple(self._deals_by_position.get(position, ()))
        if ticket is not None:
            return tuple(d for d in self.deals if d.ticket == ticket)
        start = self._to_timestamp(date_from) if date_from is not None else 0
        end = self._to_timestamp(date_to) if date_to is not None else sys.maxsize
        # Los deals se guardan en orden de tiempo, así que buscamos el rango con bisect
        return tuple(self.deals[bisect.bisect_left(self._deal_times, start):bisect.bisect_right(self._deal_times, end)])

    # ---------------- Envío de órdenes
    def order_send(self, request: dict):
        action = request.get("action")
        symbol = request.get("symbol")

        if action == self.TRADE_ACTION_REMOVE:
            if self.orders.pop(request.get("order"), None) is None:
                return self._result(request, self.TRADE_RETCODE_INVALID, "Orden no encontrada")
            return self._result(request, self.TRADE_RETCODE_DONE, "Request executed", order=request["order"])

        if symbol not in self.rates:
            return self._result(request, self.TRADE_RETCODE_INVALID, f"Símbolo {symbol} sin datos")
        if not self.is_market_open(symbol):
            return self._result(request, self.TRADE_RETCODE_MARKET_CLOSED, "Market closed")

        if action == self.TRADE_ACTION_DEAL and request.get("position"):
            return self._close_position_request(request)
        if action == self.TRADE_ACTION_DEAL:
            return self._open_position_request(request)
        if action == self.TRADE_ACTION_PENDING:
            return self._place_pending_request(request)

        return self._result(request, self.TRADE_RETCODE_INVALID, f"Acción {action} no soportada en el backtest")

    def _result(self, request: dict, retcode: int, comment: str, deal: int = 0, order: int = 0, price: float = 0.0):
        symbol = request.get("symbol")
        bid = self._current_bid(symbol) if symbol in self.rates else 0.0
        ask = self._current_ask(symbol) if symbol in self.rates else 0.0
        return TradeResult(retcode, deal, order, request.get("volume", 0.0), price, bid, ask, comment, SimpleNamespace(**request))

    def _is_valid_volume(self, symbol: str, volume: float) -> bool:
        spec = self.specs[symbol]
        steps = volume / spec["volume_step"]
        return spec["volume_min"] <= volume <= spec["volume_max"] and abs(steps - round(steps)) < 1e-6

    def _are_valid_stops(self, symbol: str, is_buy: bool, price: float, sl: float, tp: float) -> bool:
        min_distance = self.specs[symbol]["trade_stops_level"] * self.specs[symbol]["point"]
        if sl and (sl >= price - min_distance if is_buy else sl <= price + min_distance):
            return False
        if tp and (tp <= price + min_distance if is_buy else tp >= price - min_distance):
            return False
        return True

    def _open_position_request(self, request: dict):
        symbol, volume = request["symbol"], request["volume"]
        is_buy = request["type"] == self.ORDER_TYPE_BUY
        price = self._current_ask(symbol) if is_buy else self._current_bid(symbol)
        sl, tp = request.get("sl", 0.0), request.get("tp", 0.0)

        if not self._is_valid_volume(symbol, volume):
            return self._result(request, self.TRADE_RETCODE_INVALID_VOLUME, "Invalid volume")
        if not self._are_valid_stops(symbol, is_buy, price, sl, tp):
            return self._result(request, self.TRADE_RETCODE_INVALID_STOPS, "Invalid stops")

        ticket = self._open_position(symbol, request["type"], volume, price, sl, tp, request.get("magic", 0), request.get("comment", ""))
        return self._result(request, self.TRADE_RETCODE_DONE, "Request executed", deal=self._deals_by_position[ticket][-1].ticket, order=ticket, price=price)

    def _close_position_request(self, request: dict):
        position = self.positions.get(request["position"])
        if position is None:
            return self._result(request, self.TRADE_RETCODE_INVALID, "Posición no encontrada")

        is_buy = position["type"] == self.POSITION_TYPE_BUY
        price = self._current_bid(position["symbol"]) if is_buy else self._current_ask(position["symbol"])
        self._close_position(position, price, self.DEAL_REASON_EXPERT)
        # order = ticket de la posición, para que el OrderExecutor encuentre sus deals con history_deals_get(position=...)
        return self._result(request, self.TRADE_RETCODE_DONE, "Request executed", deal=self._deals_by_position[position["ticket"]][-1].ticket, order=position["ticket"], price=price)

    def _place_pending_request(self, request: dict):
        symbol, order_type, price = request["symbol"], request["type"], request["price"]
        is_buy = order_type in (self.ORDER_TYPE_BUY_LIMIT, self.ORDER_TYPE_BUY_STOP)
        market = self._current_ask(symbol) if is_buy else self._current_bid(symbol)

        valid_price = {self.ORDER_TYPE_BUY_LIMIT: price < market, self.ORDER_TYPE_BUY_STOP: price > market,
                       self.ORDER_TYPE_SELL_LIMIT: price > market, self.ORDER_TYPE_SELL_STOP: price < market}.get(order_type, False)
        if not valid_price:
            return self._result(request, self.TRADE_RETCODE_INVALID_PRICE, "Invalid price")
        if not self._is_valid_volume(symbol, request["volume"]):
            return self._result(request, self.TRADE_RETCODE_INVALID_VOLUME, "Invalid volume")
        if not self._are_valid_stops(symbol, is_buy, price, request.get("sl", 0.0), request.get("tp", 0.0)):
            return self._result(request, self.TRADE_RETCODE_INVALID_STOPS, "Invalid stops")

        ticket = self._new_ticket()
        self.orders[ticket] = {"ticket": ticket, "time_setup": self.now, "type": order_type, "magic": request.get("magic", 0),
                               "volume": request["volume"], "price_open": price, "sl": request.get("sl", 0.0),
                               "tp": request.get("tp", 0.0), "symbol": symbol, "comment": request.get("comment", "")}
        return self._result(request, self.TRADE_RETCODE_DONE, "Request executed", order=ticket, price=price)

    # ---------------- Ejecución interna
    def _new_ticket(self) -> int:
        self.next_ticket += 1
        return self.next_ticket

    def _commission(self, volume: float) -> float:
        return -round(self.commission_per_lot * volume, 2) or 0.0

    def _add_deal(self, position: dict, deal_type: int, entry: int, reason: int, price: float, profit: float, time: int,
                  comment: str = None) -> None:
        commission = self._commission(position["volume"])
        self.balance += profit + commission
        deal = Deal(self._new_ticket(), position["ticket"], time, time * 1000, deal_type, entry, position["magic"],
                    position["ticket"], reason, position["volume"], price, commission, 0.0, profit, 0.0,
                    position["symbol"], position["comment"] if comment is None else comment)
        # Los deals de SL/TP llevan la hora de su vela, que puede ser anterior al último deal: mantenemos el orden
        insert_at = bisect.bisect_right(self._deal_times, time)
        self.deals.insert(insert_at, deal)
        self._deal_times.insert(insert_at, time)
        self._deals_by_position.setdefault(position["ticket"], []).append(deal)

    def _open_position(self, symbol: str, position_type: int, volume: float, price: float, sl: float, tp: float,
                      magic: int, comment: str, time: int = None) -> int:
        ticket = self._new_ticket()
        position = {"ticket": ticket, "time": time or self.now, "type": position_type, "magic": magic, "volume": volume,
                    "price_open": price, "sl": sl, "tp": tp, "symbol": symbol, "comment": comment}
        self.positions[ticket] = position
        deal_type = self.DEAL_TYPE_BUY if position_type == self.POSITION_TYPE_BUY else self.DEAL_TYPE_SELL
        self._add_deal(position, deal_type, self.DEAL_ENTRY_IN, self.DEAL_REASON_EXPERT, price, 0.0, position["time"])
        return ticket

    def _close_position(self, position: dict, price: float, reason: int, time: int = None, comment: str = None) -> None:
        profit = self._profit(position["symbol"], position["type"], position["volume"], position["price_open"], price)
        deal_type = self.DEAL_TYPE_SELL if position["type"] == self.POSITION_TYPE_BUY else self.DEAL_TYPE_BUY
        del self.positions[position["ticket"]]
        self._add_deal(position, deal_type, self.DEAL_ENTRY_OUT, reason, price, profit, time or self.now, comment)

    def close_all_positions(self) -> None:
        """Closes every open position at the current price (end of the backtest) and removes the pending orders."""
        for position in list(self.positions.values()):
            is_buy = position["type"] == self.POSITION_TYPE_BUY
            price = self._current_bid(position["symbol"]) if is_buy else self._current_ask(position["symbol"])
            self._close_position(position, price, self.DEAL_REASON_CLIENT, comment=BACKTEST_END_COMMENT)
        self.orders.clear()

    def _process_closed_bar(self, symbol: str, bar) -> None:
        """Checks SL/TP of the open positions and the pending orders against a bar that has just closed."""
        spread = self._spread_price(symbol, bar)
        bar_time = int(bar['time'])
        # No sabemos en qué momento de la vela se tocó el nivel: usamos el final de la vela para los cierres
        close_time = min(bar_time + self.timeframe_seconds - 1, self.now - 1)
        bid_open, bid_high, bid_low = float(bar['open']), float(bar['high']), float(bar['low'])
        ask_open, ask_high, ask_low = bid_open + spread, bid_high + spread, bid_low + spread

        # 1) SL y TP de las posiciones que ya estaban abiertas al empezar la vela
        for position in [p for p in self.positions.values() if p["symbol"] == symbol and p["time"] <= bar_time]:
            sl, tp = position["sl"], position["tp"]
            if position["type"] == self.POSITION_TYPE_BUY:
                # Las compras se cierran al bid
                if sl and bid_low <= sl:
                    self._close_position(position, min(bid_open, sl), self.DEAL_REASON_SL, close_time)
                elif tp and bid_high >= tp:
                    self._close_position(position, max(bid_open, tp), self.DEAL_REASON_TP, close_time)
            else:
                # Las ventas se cierran al ask
                if sl and ask_high >= sl:
                    self._close_position(position, max(ask_open, sl), self.DEAL_REASON_SL, close_time)
                elif tp and ask_low <= tp:
                    self._close_position(position, min(ask_open, tp), self.DEAL_REASON_TP, close_time)

        # 2) Órdenes pendientes colocadas antes o al empezar la vela
        for order in [o for o in self.orders.values() if o["symbol"] == symbol and o["time_setup"] <= bar_time]:
            price, order_type = order["price_open"], order["type"]
            fill = None
            if order_type == self.ORDER_TYPE_BUY_LIMIT and ask_low <= price:
                fill = min(ask_open, price)
            elif order_type == self.ORDER_TYPE_BUY_STOP and ask_high >= price:
                fill = max(ask_open, price)
            elif order_type == self.ORDER_TYPE_SELL_LIMIT and bid_high >= price:
                fill = max(bid_open, price)
            elif order_type == self.ORDER_TYPE_SELL_STOP and bid_low <= price:
                fill = min(bid_open, price)

            if fill is not None:
                del self.orders[order["ticket"]]
                is_buy = order_type in (self.ORDER_TYPE_BUY_LIMIT, self.ORDER_TYPE_BUY_STOP)
                self._open_position(symbol, self.POSITION_TYPE_BUY if is_buy else self.POSITION_TYPE_SELL, order["volume"], fill,
                                    order["sl"], order["tp"], order["magic"], order["comment"], bar_time)
