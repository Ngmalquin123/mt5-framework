# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from events.events import DataEvent, SignalEvent
from data_provider.data_provider import DataProvider
from ..interfaces.signal_generator_interface import ISignalGenerator
from ..properties.signal_generator_properties import MACrossoverProps
from .stop_levels import StopLevels
from portfolio.portfolio import Portfolio
from order_executor.order_executor import OrderExecutor
from utils.utils import Utils
import MetaTrader5 as mt5

class SignalMACrossover(ISignalGenerator):
    
    def __init__(self, properties: MACrossoverProps):
        """
        Initializes the MACrossover object.

        Args:
            properties (MACrossoverProps): The properties object containing the parameters for the moving average crossover.

        Raises:
            Exception: If the fast period is greater than or equal to the slow period.

        """
        self.timeframe = properties.timeframe
        self.fast_period = properties.fast_period if properties.fast_period > 1 else 2
        self.slow_period = properties.slow_period if properties.slow_period > 2 else 3

        if self.fast_period >= self.slow_period:
            raise Exception(f"ERROR: el periodo rápido ({self.fast_period}) es mayor o igual al periodo lento ({self.slow_period}) para el cálculo de las medias móviles")

        # SL y TP en puntos fijos o en múltiplos del ATR (desactivados por defecto)
        self.stops = StopLevels(properties)

    
    def generate_signal(self, data_event: DataEvent, data_provider: DataProvider, portfolio: Portfolio, order_executor: OrderExecutor) -> SignalEvent:
        """
        Generates a signal based on the moving average crossover strategy.

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

        # Recuperamos los datos necesarios para calcular las medias móviles
        # Pedimos una vela más para poder calcular también las medias de la vela anterior
        # (también las necesarias para el ATR si los stops lo usan)
        num_bars = max(self.slow_period + 1, self.stops.bars_needed)
        bars = data_provider.get_latest_closed_bars(symbol, self.timeframe, num_bars)
        if bars is None or len(bars) < num_bars:
            return None

        # Recuperamos las posiciones abiertas por esta estrategia en el símbolo donde hemos tenido el Data Event
        open_positions = portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)

        # Calculamos el valor de los indicadores
        closes = bars['close']
        fast_ma = closes.iloc[-self.fast_period:].mean()
        slow_ma = closes.iloc[-self.slow_period:].mean()

        # Medias de la vela anterior: solo hay señal en la vela en la que se produce el cruce.
        # Antes había señal mientras una media estuviera por encima de la otra, y tras un SL se volvía a entrar enseguida
        prev_fast_ma = closes.iloc[-self.fast_period - 1:-1].mean()
        prev_slow_ma = closes.iloc[-self.slow_period - 1:-1].mean()

        cross_up = prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma
        cross_down = prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma

        # Detectar una señal de compra
        if open_positions['LONG'] == 0 and cross_up:
            if open_positions['SHORT'] > 0:
                # Tenemos señal de compra, pero tenemos posición de venta. Debemos cerrar la venta ANTES de abrir la compra.
                order_executor.close_strategy_short_positions_by_symbol(symbol)
                # Si la venta no se ha podido cerrar, no abrimos la compra (quedarían las dos abiertas)
                if portfolio.get_number_of_strategy_open_positions_by_symbol(symbol)['SHORT'] > 0:
                    print(f"{Utils.dateprint()} - SIGNAL: No se han podido cerrar las ventas de {symbol}. No se abre la compra")
                    return None
            signal = "BUY"

        # Señal de venta
        elif open_positions['SHORT'] == 0 and cross_down:
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
            sl, tp = 0.0, 0.0
            if self.stops.sl_points or self.stops.tp_points or self.stops.uses_atr:
                last_tick = data_provider.get_latest_tick(symbol)
                if not last_tick:
                    return None
                sl, tp = self.stops.compute(signal, last_tick, mt5.symbol_info(symbol).point, bars)

            signal_event = SignalEvent(symbol=symbol,
                                    signal=signal,
                                    target_order="MARKET",
                                    target_price=0.0,
                                    magic_number=portfolio.magic,
                                    sl=sl,
                                    tp=tp)
            
            return signal_event









