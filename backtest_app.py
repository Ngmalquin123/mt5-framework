# Backtest de la estrategia con los MISMOS módulos y la MISMA configuración (config.toml) que trading_app.py.
#
# 1) Descarga los datos (en Windows con MT5 abierto), con el timeframe de config.toml:
#    uv run python -m backtest.download_data --symbols EURUSD USDJPY GBPUSD --timeframe 1min --start 2026-08-01
# 2) Ejecuta el backtest (funciona en cualquier sistema, no necesita MT5):
#    uv run backtest_app.py                     (lee config.toml)
#    uv run backtest_app.py otra_config.toml    (para probar otros parámetros sin tocar los del real)
#
# Para probar varios timeframes y periodos de una vez: uv run python -m backtest.batch pruebas.toml

from backtest.runner import run_backtest
from utils.config import FrameworkConfig
import sys

if __name__ == "__main__":

    # Misma configuración que el real. Lo propio del backtest (fechas, balance, comisión) está en [backtest]
    CONFIG = FrameworkConfig(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    print(CONFIG.summary())

    run_backtest(CONFIG)
