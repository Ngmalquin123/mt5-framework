# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from events.events import DataEvent, SignalEvent
from data_provider.data_provider import DataProvider
from ..interfaces.signal_generator_interface import ISignalGenerator
from ..properties.signal_generator_properties import RSIProps
from .stop_levels import StopLevels
from portfolio.portfolio import Portfolio
from order_executor.order_executor import OrderExecutor
from utils.utils import Utils
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

class SignalRSI(ISignalGenerator):
    
    def __init__(self, properties: RSIProps):
        """
        Initializes the RSI Mean Reversion object.

        Args:
            properties (RSIMeanRev): The properties object containing the parameters for the RSI mean reversion.

        Raises:
            Exception: If the fast period is greater than or equal to the slow period.

        """
        self.timeframe = properties.timeframe
        self.rsi_period = properties.rsi_period if properties.rsi_period > 2 else 2   # Nos aseguramos que el periodo mínimo sea de 2

        if properties.rsi_upper > 100 or properties.rsi_upper < 0:
            self.rsi_upper = 70
        else:
            self.rsi_upper = properties.rsi_upper

        if properties.rsi_lower > 100 or properties.rsi_lower < 0:
            self.rsi_lower = 30
        else:
            self.rsi_lower = properties.rsi_lower
        
        if self.rsi_lower >= self.rsi_upper:
            raise Exception(f"ERROR: el nivel superior ({self.rsi_upper}) es menor o igual al nivel inferior ({self.rsi_lower}) para el cálculo de las señales de entrada")
        
        # SL y TP en puntos fijos o en múltiplos del ATR
        self.stops = StopLevels(properties)

        # Velas cerradas que necesita la estrategia (las clases hijas pueden necesitar más)
        self.min_bars = self.rsi_period + 1

    def _allowed_directions(self, bars: pd.DataFrame) -> tuple:
        """
        Filter of the entries. The basic RSI allows both directions; child classes can limit them
        (e.g. only buys in an uptrend).

        Args:
            bars (pd.DataFrame): The closed bars of the symbol.

        Returns:
            tuple: (allow_buy, allow_sell).
        """
        return True, True
    

    def compute_rsi(self, prices: pd.Series) -> float:

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        average_gain = np.mean(gains[-self.rsi_period:])
        average_loss = np.mean(losses[-self.rsi_period:])

        # Aplicamos formula RSI: RSI = 100 – [100 ÷ ( 1 + (Average Gain During Up Periods ÷ Average Loss During Down Periods ))]
        # Sin pérdidas el RSI es 100 (o 50 si tampoco hay ganancias). Antes devolvía 0 y daba señal de COMPRA en plena subida
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0

        rs = average_gain / average_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    
    def generate_signal(self, data_event: DataEvent, data_provider: DataProvider, portfolio: Portfolio, order_executor: OrderExecutor) -> SignalEvent:
        """
        Generates a signal based on the RSI mean reversion strategy.

        Args:
            data_event (DataEvent): The data event that triggered the signal generation.
            data_provider (DataProvider): The data provider used to retrieve the necessary data.
            portfolio (Portfolio): The portfolio containing the open positions.
            order_executor (OrderExecutor): The order executor used to execute the orders.

        Returns:
            SignalEvent: The generated signal event.
        """
        # Cogemos el símbolo del evento
        symbol = data_event.symbol

        # Recuperamos los datos necesarios para calcular el indicador del RSI
        # (también las necesarias para el ATR si los stops lo usan)
        num_bars = max(self.min_bars, self.stops.bars_needed)
        bars = data_provider.get_latest_closed_bars(symbol, self.timeframe, num_bars)
        if bars is None or len(bars) < num_bars:
            return None

        # Calculamos el RSI de las últimas velas
        rsi = self.compute_rsi(bars['close'])

        # Recuperamos las posiciones abiertas por esta estrategia en el símbolo donde hemos tenido el Data Event
        open_positions = portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)

        # Detectamos el último precio para calcular SL y TP
        last_tick = data_provider.get_latest_tick(symbol)
        if not last_tick:
            return None
        points = mt5.symbol_info(symbol).point

        # Direcciones permitidas por el filtro de la estrategia
        allow_buy, allow_sell = self._allowed_directions(bars)

        # Detectar una señal de compra
        if allow_buy and open_positions['LONG'] == 0 and rsi < self.rsi_lower:
            if open_positions['SHORT'] > 0:
                # Tenemos señal de compra, pero tenemos posición de venta. Debemos cerrar la venta ANTES de abrir la compra.
                order_executor.close_strategy_short_positions_by_symbol(symbol)
                # Si la venta no se ha podido cerrar, no abrimos la compra (quedarían las dos abiertas)
                if portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)['SHORT'] > 0:
                    print(f"{Utils.dateprint()} - SIGNAL: No se han podido cerrar las ventas de {symbol}. No se abre la compra")
                    return None
            signal = "BUY"

        # Señal de venta
        elif allow_sell and open_positions['SHORT'] == 0 and rsi > self.rsi_upper:
            if open_positions['LONG'] > 0:
                order_executor.close_strategy_long_positions_by_symbol(symbol)
                # Si la compra no se ha podido cerrar, no abrimos la venta (quedarían las dos abiertas)
                if portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)['LONG'] > 0:
                    print(f"{Utils.dateprint()} - SIGNAL: No se han podido cerrar las compras de {symbol}. No se abre la venta")
                    return None
            signal = "SELL"

        else:
            signal = ""

        # Si tenemos señal, generamos SignalEvent y lo colocamos en la cola de Eventos
        if signal != "":
            sl, tp = self.stops.compute(signal, last_tick, points, bars)
            signal_event = SignalEvent(symbol=symbol,
                                    signal=signal,
                                    target_order="MARKET",
                                    target_price=0.0,
                                    magic_number=portfolio.magic,
                                    sl=sl,
                                    tp=tp)
            
            return signal_event









