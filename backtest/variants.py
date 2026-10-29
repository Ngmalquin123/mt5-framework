# Variantes de market_structure registradas en la sección 16 de la especificación, con sus periodos y criterios.
#   1) Desarrollo (2020-2023): se ejecutan V0 (referencia), V1, V2 y V3 y se elige la variante con los criterios fijados.
#   2) Comprobación (2024-2026): solo la variante elegida y UNA sola vez. El script se niega a repetirla.
# Los criterios están aquí escritos antes de ver ningún resultado: no se cambian después.
#
# Uso (necesita los datos de 5min de EURUSD y GBPUSD desde 2020):
#   uv run python -m backtest.variants desarrollo
#   uv run python -m backtest.variants comprobacion

from backtest.analyze_trades import prepare
from backtest.runner import run_backtest
from utils.config import FrameworkConfig
from contextlib import redirect_stdout
from datetime import datetime
import argparse
import io
import json
import os
import time
import pandas as pd

VARIANTS = {
    "V0": {},                                                               # la v1, solo como referencia
    "V1": {"stop_mode": "m15_origin", "rr": 1.5, "max_sl_points": 600},     # SL detrás del origen de M15
    "V2": {"break_kinds": "bos"},                                           # solo rupturas BOS de M15
    "V3": {"confirmation": "m15_close"},                                    # confirmación con un cierre M15
}
PERIODS = {"desarrollo": ("2020-01-01", "2024-01-01"), "comprobacion": ("2024-01-01", "2026-10-01")}

# Desarrollo: candidata con al menos 50 operaciones y R medio >= +0.10. Se elige la de mayor R medio (V0 no cuenta)
MIN_TRADES_DEV, MIN_R_DEV = 50, 0.10
# Comprobación: funciona con R medio >= +0.10, profit factor >= 1.10 y al menos 30 operaciones
MIN_R_CHECK, MIN_PF_CHECK, MIN_TRADES_CHECK = 0.10, 1.10, 30

SELECTION_FILE = "variantes_seleccion.json"
CHECK_FILE = "variantes_comprobacion.json"


def run_variant(config_path: str, variant: str, period: str) -> dict:
    """
    Runs the backtest of one variant in one period.

    Args:
        config_path (str): Configuration of the strategy (estructura.toml).
        variant (str): V0, V1, V2 or V3.
        period (str): "desarrollo" or "comprobacion".

    Returns:
        dict: Trades, wins, mean and total R, profit factor, net profit and drawdown.
    """
    config = FrameworkConfig(config_path)
    if config.strategy_name != "market_structure":
        raise ValueError(f"{config_path} no usa la estrategia market_structure")
    props = config.raw["strategies"]["market_structure"]
    props.update(VARIANTS[variant])
    config.signal_props = config._build_signal_props()

    start, end = PERIODS[period]
    with redirect_stdout(io.StringIO()):
        stats = run_backtest(config, start=start, end=end, results_name=f"variantes_{period}_{variant}")

    row = {"variante": variant, "ops": stats["trades"], "% acierto": round(100 * stats["win_rate"], 1),
           "R medio": 0.0, "R total": 0.0, "PF": round(stats["profit_factor"], 2), "neto USD": round(stats["net_profit"], 2),
           "drawdown %": round(100 * stats["max_drawdown_pct"], 2), "datos": f"{stats['first_bar'][:10]} - {stats['last_bar'][:10]}"}
    if stats["trades"]:
        trades = prepare(pd.read_csv(stats["trades_file"]), pd.read_csv(stats["decisions_file"]),
                         props["server_timezone"], [tuple(w) for w in props["sessions"]])
        row["R medio"], row["R total"] = round(trades["r"].mean(), 3), round(trades["r"].sum(), 2)
    return row


def select_variant(rows: list):
    """Development criterion of section 16: the best mean R among V1-V3 with enough trades and mean R >= +0.10, or None."""
    candidates = [r for r in rows if r["variante"] != "V0" and r["ops"] >= MIN_TRADES_DEV and r["R medio"] >= MIN_R_DEV]
    return max(candidates, key=lambda r: r["R medio"])["variante"] if candidates else None


def passes_check(row: dict) -> bool:
    """Check criterion of section 16."""
    return row["R medio"] >= MIN_R_CHECK and row["PF"] >= MIN_PF_CHECK and row["ops"] >= MIN_TRADES_CHECK


def print_rows(rows: list) -> None:
    print("")
    print(pd.DataFrame(rows).set_index("variante").to_string())
    print("")


def development(config_path: str, results_dir: str) -> str:
    rows = []
    for variant in VARIANTS:
        print(f"VARIANTES: {variant} en desarrollo {PERIODS['desarrollo']}...", end=" ", flush=True)
        started = time.time()
        rows.append(run_variant(config_path, variant, "desarrollo"))
        print(f"{rows[-1]['ops']} operaciones ({time.time() - started:.0f} s)")
    print_rows(rows)

    chosen = select_variant(rows)
    with open(os.path.join(results_dir, SELECTION_FILE), "w", encoding="utf-8") as f:
        json.dump({"fecha": f"{datetime.now():%Y-%m-%d %H:%M}", "elegida": chosen, "resultados": rows}, f, indent=2, ensure_ascii=False)

    print(f" Criterio: al menos {MIN_TRADES_DEV} operaciones y R medio >= +{MIN_R_DEV:.2f}; la de mayor R medio entre V1, V2 y V3")
    if chosen is None:
        print(" RESULTADO: ninguna variante cumple el criterio. market_structure se descarta (sección 16): no hay comprobación.")
    else:
        print(f" RESULTADO: elegida {chosen}. Siguiente paso, una sola vez: uv run python -m backtest.variants comprobacion")
    return chosen


def check(config_path: str, results_dir: str) -> bool:
    check_path = os.path.join(results_dir, CHECK_FILE)
    if os.path.exists(check_path):
        with open(check_path, encoding="utf-8") as f:
            done = json.load(f)
        print(f" La comprobación ya se hizo el {done['fecha']} con {done['resultado']['variante']}: "
              f"{'FUNCIONA' if done['funciona'] else 'NO FUNCIONA'}. Solo se hace una vez (sección 16).")
        print_rows([done["resultado"]])
        return done["funciona"]

    selection_path = os.path.join(results_dir, SELECTION_FILE)
    if not os.path.exists(selection_path):
        raise SystemExit(" Falta el desarrollo: uv run python -m backtest.variants desarrollo")
    with open(selection_path, encoding="utf-8") as f:
        chosen = json.load(f)["elegida"]
    if chosen is None:
        raise SystemExit(" En el desarrollo ninguna variante cumplió el criterio: no hay nada que comprobar")

    print(f"VARIANTES: {chosen} en comprobación {PERIODS['comprobacion']}...", flush=True)
    row = run_variant(config_path, chosen, "comprobacion")
    works = passes_check(row)
    with open(check_path, "w", encoding="utf-8") as f:
        json.dump({"fecha": f"{datetime.now():%Y-%m-%d %H:%M}", "resultado": row, "funciona": works}, f, indent=2, ensure_ascii=False)

    print_rows([row])
    print(f" Criterio: R medio >= +{MIN_R_CHECK:.2f}, profit factor >= {MIN_PF_CHECK:.2f} y al menos {MIN_TRADES_CHECK} operaciones")
    if works:
        print(f" RESULTADO: FUNCIONA. Siguiente paso: Fase 4 (PAPER, sin dinero) con {chosen}. No es una prueba de ventaja: es el mínimo para seguir.")
    else:
        print(" RESULTADO: NO FUNCIONA. market_structure se descarta (sección 16).")
    print(f" Detalle: uv run python -m backtest.analyze_trades variantes_comprobacion_{chosen}")
    return works


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Variantes de market_structure (sección 16 de la especificación)")
    parser.add_argument("periodo", choices=["desarrollo", "comprobacion"])
    parser.add_argument("--config", default="estructura.toml")
    parser.add_argument("--results-dir", default="backtest/results")
    args = parser.parse_args()

    pd.set_option("display.width", 200)
    os.makedirs(args.results_dir, exist_ok=True)
    if args.periodo == "desarrollo":
        development(args.config, args.results_dir)
    else:
        check(args.config, args.results_dir)
