# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from pydantic import BaseModel, ConfigDict

class BaseRiskProps(BaseModel):
    model_config = ConfigDict(extra="forbid")

class MaxLeverageFactorRiskProps(BaseRiskProps):
    """
    Risk properties for managing maximum leverage factor.
    """

    max_leverage_factor: float

class TradingLimitsProps(BaseModel):
    """
    General risk limits checked before any order, whatever the risk management method.
    A value of 0 (or an empty string for the hours) disables that limit.

    Attributes:
        max_daily_loss_pct (float): Max loss of the day as a fraction of the balance at the start of the day (0.02 = 2%).
        max_open_positions (int): Max number of positions opened by the strategy at the same time.
        max_spread_points (int): Max spread (in points) allowed to open a position.
        no_trading_start (str): Start of the period without new trades, server time "HH:MM".
        no_trading_end (str): End of the period without new trades, server time "HH:MM".
    """
    model_config = ConfigDict(extra="forbid")

    max_daily_loss_pct: float = 0.02
    max_open_positions: int = 3
    max_spread_points: int = 20
    no_trading_start: str = "23:45"
    no_trading_end: str = "01:15"