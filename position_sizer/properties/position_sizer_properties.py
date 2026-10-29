# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from pydantic import BaseModel, ConfigDict

class BaseSizerProps(BaseModel):
    model_config = ConfigDict(extra="forbid")

class MinSizingProps(BaseSizerProps):
    pass

class FixedSizingProps(BaseSizerProps):
    """
    Represents the properties for fixed sizing of positions.

    Attributes:
        volume (float): The fixed volume for each position.
    """
    volume: float

class RiskPctSizingProps(BaseSizerProps):
    """
    Properties for risk percentage position sizing.

    Attributes:
        risk_pct (float): The risk percentage for position sizing.
        round_down (bool): If True, the volume is rounded down to the volume step (never more risk than risk_pct; if the
            minimum volume of the broker exceeds it, no trade). If False, to the nearest step (it can risk a bit more).
    """
    risk_pct: float
    round_down: bool = False