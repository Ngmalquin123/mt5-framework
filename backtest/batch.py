# Pruebas en lote: la misma configuración en varios timeframes, con un periodo de práctica y otro de comprobación.
#
# Uso (desde la carpeta del proyecto):
#   uv run python -m backtest.batch pruebas.toml --timeframes 5min 15min 30min 1h
#   uv run python -m backtest.batch pruebas.toml --timeframes 1h 4h --split 2025-06-01
#
# Práctica: de [backtest] start a split (aquí se eligen los parámetros).
# Comprobación: de split a [backtest] end (aquí se comprueba, SIN cambiar nada, si lo elegido sigue funcionando).

from backtest.runner import run_backtest
from utils.config import FrameworkConfig
from contextlib import redirect_stdout
from datetime import datetime
import argparse
import io
import time


def run_batch(config: FrameworkConfig, timeframes: list, split: str = None) -> list:
    """
    Runs the backtest of the configuration for each timeframe and period.

    Args:
        config (FrameworkConfig): The configuration to test.
        timeframes (list): Timeframes to test.
        split (str, optional): Date that separates the practice and the check periods. Without it, one period.

    Returns:
        list: One dict per backtest with the timeframe, the period and the statistics (or the error).
    """
    start, end = config.backtest.get("start"), config.backtest.get("end")
    periods = [("práctica", start, split), ("comprobación", split, end)] if split else [("completo", start, end)]
    batch_name = f"batch_{datetime.now():%Y%m%d_%H%M%S}"

    rows = []
    for timeframe in timeframes:
        for period_name, period_start, period_end in periods:
            print(f"BATCH: {timeframe} {period_name} ({period_start} - {period_end})...", end=" ", flush=True)
            started = time.time()
            row = {"timeframe": timeframe, "period": period_name, "start": period_start, "end": period_end}
            try:
                # Los mensajes y el informe de cada backtest se descartan: solo mostramos la tabla final
                with redirect_stdout(io.StringIO()):
                    row["stats"] = run_backtest(config, timeframe=timeframe, start=period_start, end=period_end,
                                                results_name=f"{batch_name}_{timeframe}_{period_name}")
            except FileNotFoundError:
                row["error"] = "sin datos"
            except ValueError as e:
                row["error"] = "sin velas en el periodo" if "No hay velas" in str(e) else str(e)
            print(f"{row.get('error', 'ok')} ({time.time() - started:.0f} s)")
            rows.append(row)

    return rows


def print_table(config: FrameworkConfig, rows: list) -> None:
    print("")
    print(f" {config.summary()}")
    print("=" * 96)
    print(f" {'timeframe':<10} {'periodo':<13} {'resultado':>10} {'PF':>6} {'drawdown':>9} {'operac.':>8} {'% acierto':>10}   desde - hasta")
    print("-" * 96)
    for row in rows:
        dates = f"{row['start'] or ''} - {row['end'] or ''}"
        if "error" in row:
            print(f" {row['timeframe']:<10} {row['period']:<13} {row['error']:>48}   {dates}")
            continue
        s = row["stats"]
        profit_factor = f"{s['profit_factor']:.2f}" if s['profit_factor'] != float("inf") else "inf"
        print(f" {row['timeframe']:<10} {row['period']:<13} {s['return_pct']:>+10.2%} {profit_factor:>6} {s['max_drawdown_pct']:>9.2%} "
              f"{s['trades']:>8} {s['win_rate']:>10.2%}   {dates}")
    print("=" * 96)

    missing = sorted({row["timeframe"] for row in rows if row.get("error") == "sin datos"})
    if missing:
        print(" Faltan datos. Descárgalos con MT5 abierto, por ejemplo:")
        for timeframe in missing:
            print(f"   uv run python -m backtest.download_data --symbols {' '.join(config.symbols)} --timeframe {timeframe} "
                  f"--start {config.backtest.get('start', '2025-01-01')}")
    print(" Profit factor > 1 gana dinero. Busca resultados parecidos en práctica y comprobación, y en timeframes vecinos.")
    print(" Las operaciones de cada prueba están en backtest/results/")
    print("")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtests de una configuración en varios timeframes y periodos")
    parser.add_argument("config", nargs="?", default="config.toml", help="Archivo de configuración (por defecto config.toml)")
    parser.add_argument("--timeframes", nargs="+", default=["5min", "15min", "30min", "1h"], help="Timeframes a probar")
    parser.add_argument("--split", default=None, help="Fecha que separa práctica y comprobación (por defecto [backtest] split)")
    args = parser.parse_args()

    CONFIG = FrameworkConfig(args.config)
    rows = run_batch(CONFIG, args.timeframes, args.split or CONFIG.backtest.get("split"))
    print_table(CONFIG, rows)
