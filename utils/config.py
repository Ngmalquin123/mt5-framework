# Lectura de config.toml: convierte cada sección en las properties que usan los módulos del Framework.
# No importa MetaTrader5, así backtest_app.py puede leer la configuración antes de activar el MT5 simulado.

from signal_generator.properties.signal_generator_properties import BaseSignalProps, MACrossoverProps, RSIProps
from position_sizer.properties.position_sizer_properties import BaseSizerProps, MinSizingProps, FixedSizingProps, RiskPctSizingProps
from risk_manager.properties.risk_manager_properties import MaxLeverageFactorRiskProps, TradingLimitsProps
import tomllib


class FrameworkConfig():

    STRATEGIES = {"rsi": RSIProps, "ma_crossover": MACrossoverProps}

    def __init__(self, path: str = "config.toml"):
        """
        Reads the configuration file and builds the properties of each module.

        Args:
            path (str): Path of the TOML configuration file.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If a section or a value is missing or not valid.
        """
        self.path = path
        with open(path, "rb") as f:
            self.raw: dict = tomllib.load(f)

        trading = self._section("trading")
        self.symbols: list = trading["symbols"]
        self.timeframe: str = trading["timeframe"]
        self.magic_number: int = trading["magic_number"]
        self.strategy_name: str = trading["strategy"]

        self.signal_props: BaseSignalProps = self._build_signal_props()
        self.sizing_props: BaseSizerProps = self._build_sizing_props()
        self.risk_props = MaxLeverageFactorRiskProps(**self._section("risk"))
        self.trading_limits = TradingLimitsProps(**self.raw.get("limits", {}))
        self.pending_order_max_age_minutes: float = self.raw.get("orders", {}).get("pending_order_max_age_minutes", 0)
        self.journal_file: str = self.raw.get("journal", {}).get("file", "logs/trades.csv")
        self.backtest: dict = self.raw.get("backtest", {})

    def _section(self, name: str) -> dict:
        try:
            return self.raw[name]
        except KeyError:
            raise ValueError(f"Falta la sección [{name}] en {self.path}")

    def _build_signal_props(self) -> BaseSignalProps:
        if self.strategy_name not in self.STRATEGIES:
            raise ValueError(f"Estrategia '{self.strategy_name}' no válida en {self.path}. Opciones: {', '.join(self.STRATEGIES)}")

        params = self._section("strategies").get(self.strategy_name)
        if params is None:
            raise ValueError(f"Falta la sección [strategies.{self.strategy_name}] en {self.path}")

        return self.STRATEGIES[self.strategy_name](timeframe=self.timeframe, **params)

    def _build_sizing_props(self) -> BaseSizerProps:
        sizing = self._section("sizing")
        method = sizing.get("method")

        if method == "fixed":
            return FixedSizingProps(volume=sizing["volume"])
        elif method == "min":
            return MinSizingProps()
        elif method == "risk_pct":
            return RiskPctSizingProps(risk_pct=sizing["risk_pct"])
        else:
            raise ValueError(f"Método de sizing '{method}' no válido en {self.path}. Opciones: fixed, min, risk_pct")

    def summary(self) -> str:
        """Short description of the configuration, to print it at start."""
        return (f"Config {self.path}: {self.strategy_name} en {', '.join(self.symbols)} ({self.timeframe}) | "
                f"{self.signal_props.model_dump(exclude={'timeframe'})} | sizing {self.raw['sizing']['method']}")
