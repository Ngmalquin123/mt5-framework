# Cálculo del SL y TP de las señales: en puntos fijos o en múltiplos del ATR

from ..properties.signal_generator_properties import StopLevelsProps
import pandas as pd


class StopLevels():

    def __init__(self, properties: StopLevelsProps):
        """
        Initializes the StopLevels object.

        Args:
            properties (StopLevelsProps): SL/TP parameters of the strategy.
        """
        self.sl_points = max(properties.sl_points, 0)
        self.tp_points = max(properties.tp_points, 0)
        self.sl_atr = max(properties.sl_atr, 0.0)
        self.tp_atr = max(properties.tp_atr, 0.0)
        self.atr_period = max(properties.atr_period, 1)

    @property
    def uses_atr(self) -> bool:
        return self.sl_atr > 0 or self.tp_atr > 0

    @property
    def bars_needed(self) -> int:
        """Closed bars needed to compute the ATR (one more for the previous close)."""
        return self.atr_period + 1 if self.uses_atr else 0

    @staticmethod
    def compute_atr(bars: pd.DataFrame, period: int) -> float:
        """
        Computes the ATR (Average True Range): the average range of the last bars, including gaps.

        Args:
            bars (pd.DataFrame): Closed bars with the columns high, low and close.
            period (int): Number of bars to average.

        Returns:
            float: The ATR, in price units.
        """
        previous_close = bars['close'].shift(1)
        # True Range: el mayor de (máximo - mínimo), |máximo - cierre anterior| y |mínimo - cierre anterior|
        true_range = pd.concat([bars['high'] - bars['low'],
                                (bars['high'] - previous_close).abs(),
                                (bars['low'] - previous_close).abs()], axis=1).max(axis=1)
        return float(true_range.iloc[-period:].mean())

    def compute(self, signal: str, last_tick: dict, point: float, bars: pd.DataFrame = None) -> tuple:
        """
        Computes the SL and TP prices of a market order.

        Args:
            signal (str): "BUY" or "SELL".
            last_tick (dict): Last tick of the symbol (bid and ask).
            point (float): Point of the symbol.
            bars (pd.DataFrame, optional): Closed bars, needed when the ATR is used.

        Returns:
            tuple: (sl, tp). 0.0 means no SL or no TP.
        """
        atr = self.compute_atr(bars, self.atr_period) if self.uses_atr else 0.0

        # Distancia al precio: con ATR si está activado, si no en puntos
        sl_distance = self.sl_atr * atr if self.sl_atr > 0 else self.sl_points * point
        tp_distance = self.tp_atr * atr if self.tp_atr > 0 else self.tp_points * point

        # Las compras entran al ask y las ventas al bid
        if signal == "BUY":
            sl = last_tick['ask'] - sl_distance if sl_distance > 0 else 0.0
            tp = last_tick['ask'] + tp_distance if tp_distance > 0 else 0.0
        else:
            sl = last_tick['bid'] + sl_distance if sl_distance > 0 else 0.0
            tp = last_tick['bid'] - tp_distance if tp_distance > 0 else 0.0

        return sl, tp
