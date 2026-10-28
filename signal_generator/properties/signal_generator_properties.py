# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from pydantic import BaseModel, ConfigDict

class BaseSignalProps(BaseModel):
    # Un parámetro mal escrito en config.toml (p.ej. "sl_pionts") da error en lugar de ignorarse
    model_config = ConfigDict(extra="forbid")

class StopLevelsProps(BaseSignalProps):
    """
    SL and TP of the signals: fixed points or multiples of the ATR.
    If sl_atr / tp_atr is greater than 0 the ATR is used and the points are ignored. 0 disables the SL or the TP.

    Attributes:
        sl_points (int): SL distance in points.
        tp_points (int): TP distance in points.
        sl_atr (float): SL distance as a multiple of the ATR (e.g. 1.5).
        tp_atr (float): TP distance as a multiple of the ATR (e.g. 3.0).
        atr_period (int): Number of bars used to compute the ATR.
    """
    sl_points: int = 0
    tp_points: int = 0
    sl_atr: float = 0.0
    tp_atr: float = 0.0
    atr_period: int = 14

class MACrossoverProps(StopLevelsProps):
    """
    Represents the properties for a Moving Average Crossover signal generator.

    Attributes:
        timeframe (str): The timeframe for the signal generator.
        fast_period (int): The period for the fast moving average.
        slow_period (int): The period for the slow moving average.
        (plus the SL/TP parameters of StopLevelsProps, disabled by default)
    """
    timeframe: str
    fast_period: int
    slow_period: int

class RSIProps(StopLevelsProps):
    timeframe: str
    rsi_period: int
    rsi_upper: float
    rsi_lower: float
    sl_points: int
    tp_points: int

class RSITrendProps(RSIProps):
    """
    RSI with a trend filter: only buys when the price is above the moving average of trend_period bars
    (buying the pullbacks of an uptrend) and only sells when it is below.

    Attributes:
        trend_period (int): Bars of the moving average that defines the trend (e.g. 200).
        (plus all the parameters of RSIProps)
    """
    trend_period: int = 200
