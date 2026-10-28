# Ruptura del rango de una sesión: marca el máximo y el mínimo de la sesión asiática y opera su ruptura en la apertura de Londres

from events.events import DataEvent, SignalEvent
from data_provider.data_provider import DataProvider
from ..interfaces.signal_generator_interface import ISignalGenerator
from ..properties.signal_generator_properties import SessionBreakoutProps
from .stop_levels import StopLevels
from portfolio.portfolio import Portfolio
from order_executor.order_executor import OrderExecutor
from utils.utils import Utils
from datetime import datetime, time, timezone
from typing import Dict
import MetaTrader5 as mt5


class SignalSessionBreakout(ISignalGenerator):

    _TIMEFRAME_MINUTES = {'1min': 1, '2min': 2, '3min': 3, '4min': 4, '5min': 5, '6min': 6, '10min': 10, '12min': 12,
                          '15min': 15, '20min': 20, '30min': 30, '1h': 60, '2h': 120, '3h': 180, '4h': 240}

    def __init__(self, properties: SessionBreakoutProps):
        """
        Initializes the session breakout object.

        Args:
            properties (SessionBreakoutProps): Hours of the session (server time) and SL/TP parameters.

        Raises:
            Exception: If the timeframe is not intraday or the hours are not consistent.
        """
        if properties.timeframe not in self._TIMEFRAME_MINUTES:
            raise Exception(f"ERROR: La ruptura de sesión necesita un timeframe intradía (hasta 4h), no {properties.timeframe}")

        self.timeframe = properties.timeframe
        self.range_start = self._parse_time(properties.range_start)
        self.range_end = self._parse_time(properties.range_end)
        self.entry_end = self._parse_time(properties.entry_end)
        self.close_time = self._parse_time(properties.close_time) if properties.close_time else None

        if not self.range_start < self.range_end <= self.entry_end:
            raise Exception(f"ERROR: Las horas deben cumplir range_start < range_end <= entry_end "
                            f"({properties.range_start}, {properties.range_end}, {properties.entry_end})")

        self.sl_at_range = properties.sl_at_range
        self.tp_range_mult = max(properties.tp_range_mult, 0.0)
        self.stops = StopLevels(properties)

        # Velas de un día completo para tener todo el rango (y las del ATR si se usa)
        self.num_bars = max(24 * 60 // self._TIMEFRAME_MINUTES[self.timeframe] + 2, self.stops.bars_needed)

        # Día de la última entrada de cada símbolo: como mucho una operación por símbolo y día
        self.last_entry_day: Dict[str, datetime.date] = {}

    @staticmethod
    def _parse_time(value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()

    def generate_signal(self, data_event: DataEvent, data_provider: DataProvider, portfolio: Portfolio, order_executor: OrderExecutor) -> SignalEvent:
        """
        Generates a signal when the price closes outside the range of the session.

        Args:
            data_event (DataEvent): The data event that triggered the signal generation.
            data_provider (DataProvider): The data provider used to retrieve the necessary data.
            portfolio (Portfolio): The portfolio containing the open positions.
            order_executor (OrderExecutor): The order executor used to close the positions at close_time.

        Returns:
            SignalEvent: The generated signal event, or None.
        """
        symbol = data_event.symbol

        # Hora actual del servidor (la del último tick)
        last_tick = data_provider.get_latest_tick(symbol)
        if not last_tick:
            return None
        now = datetime.fromtimestamp(last_tick['time'], tz=timezone.utc).replace(tzinfo=None)

        open_positions = portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)

        # 1) A la hora de cierre cerramos lo que siga abierto (no dejamos posiciones de un día para otro)
        if self.close_time is not None and now.time() >= self.close_time:
            if open_positions['TOTAL'] > 0:
                print(f"{Utils.dateprint()} - SIGNAL: Hora de cierre ({self.close_time:%H:%M}). Cerrando posiciones de {symbol}")
                order_executor.close_strategy_long_positions_by_symbol(symbol)
                order_executor.close_strategy_short_positions_by_symbol(symbol)
            return None

        # 2) Solo buscamos entradas dentro de la ventana, una vez al día y sin posición abierta
        if not self.range_end <= now.time() < self.entry_end:
            return None
        if self.last_entry_day.get(symbol) == now.date() or open_positions['TOTAL'] > 0:
            return None

        # 3) Rango de la sesión de hoy
        bars = data_provider.get_latest_closed_bars(symbol, self.timeframe, self.num_bars)
        if bars is None or bars.empty:
            return None

        today_bars = bars[bars.index.date == now.date()]
        bar_times = today_bars.index.time
        session = today_bars[(bar_times >= self.range_start) & (bar_times < self.range_end)]
        if session.empty:
            return None

        range_high = session['high'].max()
        range_low = session['low'].min()

        # 4) Ruptura: la última vela cerrada termina fuera del rango
        last_close = bars['close'].iloc[-1]
        if last_close > range_high:
            signal = "BUY"
        elif last_close < range_low:
            signal = "SELL"
        else:
            return None

        # SL y TP: por puntos/ATR y, si está activado, en el rango
        sl, tp = self.stops.compute(signal, last_tick, mt5.symbol_info(symbol).point, bars)
        entry_price = last_tick['ask'] if signal == "BUY" else last_tick['bid']
        range_height = range_high - range_low

        if self.sl_at_range:
            # SL en el lado contrario del rango
            sl = range_low if signal == "BUY" else range_high
        if self.tp_range_mult > 0:
            # TP a una (o varias) alturas del rango desde la entrada
            tp = entry_price + self.tp_range_mult * range_height if signal == "BUY" else entry_price - self.tp_range_mult * range_height

        self.last_entry_day[symbol] = now.date()

        return SignalEvent(symbol=symbol,
                           signal=signal,
                           target_order="MARKET",
                           target_price=0.0,
                           magic_number=portfolio.magic,
                           sl=sl,
                           tp=tp)
