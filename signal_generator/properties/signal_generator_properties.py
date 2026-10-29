# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from pydantic import BaseModel, ConfigDict
from typing import List, Literal, Tuple

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

class MarketStructureProps(BaseSignalProps):
    """
    Market structure without indicators: trend in H1, break and pullback in M15, confirmation and entry in M5.
    The rules (ST-, EN-, SL-, TP-, HO-) are the ones of the specification of phase 1. It needs timeframe = "5min":
    the M15 and H1 bars are built from the M5 ones.

    Attributes:
        server_timezone (str): Time zone of the server (HO-1). RoboForex: "America/New_York+7".
        swing_n_h1, swing_n_m15, swing_n_m5 (int): Bars at each side of a pivot (SW-6).
        break_margin_points (float): The close must pass the level by this margin (BOS-1).
        zone_tol_points (float): Half width of the pullback zone around the broken M15 level (ST-3).
        retrace_max_m15 (int): M15 bars to touch the zone (ST-5).
        confirm_max_m5 (int): M5 bars for the confirmation after the touch (ST-10).
        sl_buffer_points (float): Margin of the SL beyond the M5 HL / LH (SL-1).
        min_sl_points, max_sl_points (float): Accepted SL distance (SL-2). The min is also 3 x spread and the stops level.
        rr (float): Take profit as a multiple of the SL distance (TP-1).
        max_entry_slip_points (float): Max distance between the entry price and the close of the signal bar (EN-2).
        sessions (list): Entry windows in local time: [time zone, "HH:MM" start, "HH:MM" end] (HO-2).
        intraday_close (str): New York time at which the open positions are closed (HO-3).
        friday_entry_end (str): New York time from which no trades are opened on Fridays (HO-3).
        warmup_days (int): Days of M5 history read at start to build the structure.
        stop_mode (str): "m5_swing" (SL-1) or "m15_origin" (variant V1: SL beyond the origin of the M15 break).
        break_kinds (str): "any" (ST-2) or "bos" (variant V2: only the M15 breaks labeled as BOS).
        confirmation (str): "m5_sequence" (ST-7, ST-8) or "m15_close" (variant V3: an M15 close beyond the previous M15 bar).
    """
    timeframe: str
    server_timezone: str = "America/New_York+7"
    swing_n_h1: int = 3
    swing_n_m15: int = 3
    swing_n_m5: int = 2
    break_margin_points: float = 5
    zone_tol_points: float = 30
    retrace_max_m15: int = 16
    confirm_max_m5: int = 36
    sl_buffer_points: float = 20
    min_sl_points: float = 50
    max_sl_points: float = 300
    rr: float = 2.0
    max_entry_slip_points: float = 20
    sessions: List[Tuple[str, str, str]] = [("Europe/London", "08:00", "12:00"), ("America/New_York", "08:00", "11:00")]
    intraday_close: str = "16:00"
    friday_entry_end: str = "12:00"
    warmup_days: int = 30
    # Variantes de la sección 16 de la especificación. Los valores por defecto son la v1
    stop_mode: Literal["m5_swing", "m15_origin"] = "m5_swing"
    break_kinds: Literal["any", "bos"] = "any"
    confirmation: Literal["m5_sequence", "m15_close"] = "m5_sequence"
