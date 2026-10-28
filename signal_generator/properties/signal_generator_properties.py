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

class SessionBreakoutProps(StopLevelsProps):
    """
    Breakout of the range of a session (by default the Asian session, at the London open).
    All the hours are SERVER time ("HH:MM").

    Attributes:
        timeframe (str): Timeframe of the bars (intraday: 1min to 4h).
        range_start (str): Start of the session that forms the range.
        range_end (str): End of the range. From here the breakouts are traded.
        entry_end (str): No new entries from this hour.
        close_time (str): The open positions are closed at this hour. "" = they are not closed.
        sl_at_range (bool): If True, the SL is placed at the opposite side of the range (instead of points/ATR).
        tp_range_mult (float): If > 0, TP at this multiple of the range height from the entry (instead of points/ATR).
        (plus the SL/TP parameters of StopLevelsProps, used when sl_at_range is False or tp_range_mult is 0)
    """
    timeframe: str
    range_start: str = "02:00"
    range_end: str = "10:00"
    entry_end: str = "14:00"
    close_time: str = "22:00"
    sl_at_range: bool = True
    tp_range_mult: float = 1.0
