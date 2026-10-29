# Velas de un timeframe mayor (M15, H1) construidas a partir de velas M5 cerradas (CV-2).
# Así los tres timeframes están sincronizados y una vela M15 o H1 solo existe cuando ya han cerrado todas sus velas M5.

from typing import List, NamedTuple, Optional
import pandas as pd


class Bar(NamedTuple):
    time: int           # hora de apertura (servidor, segundos)
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0


class BarAggregator():

    def __init__(self, timeframe_seconds: int, base_seconds: int = 300):
        """
        Initializes the aggregator.

        Args:
            timeframe_seconds (int): Duration of the bars to build (900 = M15, 3600 = H1).
            base_seconds (int): Duration of the bars received (300 = M5).
        """
        if timeframe_seconds % base_seconds:
            raise ValueError(f"{timeframe_seconds} s no es múltiplo de {base_seconds} s")
        self.timeframe_seconds = timeframe_seconds
        self.base_seconds = base_seconds
        self.current: Optional[Bar] = None

    def update(self, bar: Bar) -> List[Bar]:
        """
        Adds one closed base bar.

        Args:
            bar (Bar): The closed base bar (for example M5).

        Returns:
            List[Bar]: The bars of the bigger timeframe that are complete with this bar (usually none or one).
        """
        bucket = bar.time - bar.time % self.timeframe_seconds
        completed = []

        # Si falta la última vela M5 del periodo (sin ticks), el periodo se cierra al llegar la primera del siguiente:
        # se entrega tarde, nunca antes de tiempo
        if self.current is not None and self.current.time != bucket:
            completed.append(self.current)
            self.current = None

        if self.current is None:
            self.current = Bar(bucket, bar.open, bar.high, bar.low, bar.close, bar.tick_volume, bar.spread)
        else:
            c = self.current
            self.current = Bar(c.time, c.open, max(c.high, bar.high), min(c.low, bar.low), bar.close,
                               c.tick_volume + bar.tick_volume, bar.spread)

        if bar.time + self.base_seconds >= bucket + self.timeframe_seconds:
            completed.append(self.current)
            self.current = None
        return completed


def aggregate(bars: pd.DataFrame, timeframe_seconds: int, base_seconds: int = 300) -> pd.DataFrame:
    """
    Builds the closed bars of a bigger timeframe from a DataFrame of base bars (time, open, high, low, close...).
    The last period is left out if it is not complete.
    """
    aggregator = BarAggregator(timeframe_seconds, base_seconds)
    result = []
    for row in bars.itertuples(index=False):
        result += aggregator.update(Bar(int(row.time), row.open, row.high, row.low, row.close,
                                        int(getattr(row, "tick_volume", 0)), int(getattr(row, "spread", 0))))
    return pd.DataFrame(result, columns=Bar._fields)
