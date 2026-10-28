# RSI con filtro de tendencia: opera los extremos del RSI solo a favor de la tendencia de una media larga

from ..properties.signal_generator_properties import RSITrendProps
from .signal_rsi_mr import SignalRSI
import pandas as pd


class SignalRSITrend(SignalRSI):

    def __init__(self, properties: RSITrendProps):
        """
        Initializes the RSI with trend filter object.

        Args:
            properties (RSITrendProps): The parameters of the RSI, the SL/TP and the trend moving average.
        """
        super().__init__(properties)
        self.trend_period = properties.trend_period if properties.trend_period > 1 else 2

        # Hacen falta las velas de la media de tendencia
        self.min_bars = max(self.min_bars, self.trend_period)

    def _allowed_directions(self, bars: pd.DataFrame) -> tuple:
        """
        Only buys above the trend moving average and only sells below it.

        Args:
            bars (pd.DataFrame): The closed bars of the symbol.

        Returns:
            tuple: (allow_buy, allow_sell).
        """
        trend_ma = bars['close'].iloc[-self.trend_period:].mean()
        last_close = bars['close'].iloc[-1]

        # Tendencia alcista: el RSI bajo es un retroceso para comprar. Bajista: el RSI alto es un rebote para vender
        return last_close > trend_ma, last_close < trend_ma
