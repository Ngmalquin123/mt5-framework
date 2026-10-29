# Análisis de un backtest de market_structure a partir de sus dos CSV (operaciones y decisiones):
#   1) Operaciones por motivo de salida (TP, SL, cierre intradía), por sesión, por hora y día de entrada y por par.
#      El resultado se da también en R (múltiplos del riesgo): R = 1 es ganar lo que se arriesgaba hasta el SL.
#   2) Embudo de los setups por par: rupturas, toques, señales y en qué regla se paró el resto.
#
# Solo describe lo que pasó en el backtest. Con pocas operaciones las diferencias entre grupos pueden ser suerte:
# no sirve para elegir parámetros (eso se hace en la Fase 5, con periodos separados).
#
# Uso:
#   uv run python -m backtest.analyze_trades                              (el último backtest de backtest/results)
#   uv run python -m backtest.analyze_trades backtest_20261028_232443     (uno concreto)
#   uv run python -m backtest.analyze_trades --config estructura.toml     (de aquí salen la zona horaria y las sesiones)

from market_structure.sessions import in_windows, local_time
from datetime import datetime, timezone
import argparse
import glob
import os
import re
import tomllib
import pandas as pd

ZONE_NAMES = {"Europe/London": "Londres", "America/New_York": "Nueva York"}
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
STOP_RULES = ["ST-5", "ST-6", "ST-10", "HO-2", "HO-3", "RG-2", "EN-2", "SL-1", "SL-2", "TP-2", "EN-3/EN-4"]
SENT = re.compile(r"EN-1: (BUY|SELL) a ([\d.]+), SL ([\d.]+), TP ([\d.]+)")


def server_seconds(text: str) -> int:
    """Times of the CSV files are server time written as if it were UTC (as MT5 gives them)."""
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())


def latest_results(results_dir: str) -> str:
    files = sorted(glob.glob(os.path.join(results_dir, "*_trades.csv")))
    if not files:
        raise SystemExit(f"No hay ningún *_trades.csv en {results_dir}")
    return os.path.basename(files[-1])[:-len("_trades.csv")]


def attach_signals(trades: pd.DataFrame, decisions: pd.DataFrame) -> pd.DataFrame:
    """Adds the SL and TP of each trade (from its EN-1 line) and the result in R."""
    sent = decisions[decisions["reason"].str.startswith("EN-1")].copy()
    parts = sent["reason"].str.extract(SENT)
    sent["sl"], sent["tp"] = parts[2].astype(float), parts[3].astype(float)
    sent["entry_seconds"] = sent["time"].map(server_seconds)

    # La orden se envía en la misma vela en la que se ejecuta: la EN-1 del par es la última antes de la entrada
    trades = pd.merge_asof(trades.sort_values("entry_seconds"), sent[["entry_seconds", "symbol", "sl", "tp"]].sort_values("entry_seconds"),
                           on="entry_seconds", by="symbol", direction="backward", tolerance=600)
    sign = trades["side"].map({"BUY": 1, "SELL": -1})
    trades["r"] = sign * (trades["exit_price"] - trades["entry_price"]) / (trades["entry_price"] - trades["sl"]).abs()
    return trades


def prepare(trades: pd.DataFrame, decisions: pd.DataFrame, server_timezone: str, sessions: list) -> pd.DataFrame:
    trades = trades.copy()
    trades["entry_seconds"] = trades["entry_time"].map(server_seconds)
    trades["minutes"] = (trades["exit_time"].map(server_seconds) - trades["entry_seconds"]) / 60
    ny = trades["entry_seconds"].map(lambda t: local_time(t, server_timezone, "America/New_York"))
    trades["hora NY"] = ny.map(lambda t: f"{t.hour:02d}h")
    trades["día"] = pd.Categorical(ny.map(lambda t: WEEKDAYS[t.weekday()]), categories=WEEKDAYS, ordered=True)

    def session(t: int) -> str:
        for window in sessions:
            if in_windows(t, server_timezone, [window]):
                return ZONE_NAMES.get(window[0], window[0])
        return "fuera"
    trades["sesión"] = trades["entry_seconds"].map(session)

    if decisions is not None:
        trades = attach_signals(trades, decisions)
    else:
        trades["r"] = float("nan")
    return trades


def summary(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    groups = trades.groupby(by, observed=True)
    table = pd.DataFrame({
        "ops": groups.size(),
        "ganan": groups["net_profit"].apply(lambda p: int((p > 0).sum())),
        "% acierto": groups["net_profit"].apply(lambda p: round(100 * (p > 0).mean(), 1)),
        "R medio": groups["r"].mean().round(2),
        "R total": groups["r"].sum(min_count=1).round(2),
        "neto USD": groups["net_profit"].sum().round(2),
        "min. medios": groups["minutes"].mean().round(0),
    })
    return table


def funnel(decisions: pd.DataFrame) -> pd.DataFrame:
    """Setups of each symbol: how many reached each step and where the others stopped (same counts as the backtest report)."""
    rules = decisions.assign(rule=decisions["reason"].str.split(":").str[0])
    counts = rules.groupby(["symbol", "rule"]).size().unstack(fill_value=0)
    get = lambda rule: counts[rule] if rule in counts else 0
    table = pd.DataFrame({"rupturas": get("ST-2") + get("ST-6 (reemplazo)"), "toques": get("ST-4"), "señales": get("ST-8"),
                          "enviadas": get("EN-1"), "ejecutadas": get("EN-4")})
    for rule in STOP_RULES:
        if rule in counts and counts[rule].sum():
            table[rule] = counts[rule]
    table.loc["TOTAL"] = table.sum()
    return table


def print_table(title: str, table: pd.DataFrame) -> None:
    print(f"\n--- {title}")
    print(table.to_string())


def analyze(name: str, results_dir: str = "backtest/results", config_path: str = "estructura.toml") -> dict:
    """
    Prints the analysis of one backtest.

    Args:
        name (str): Name of the result files, without _trades.csv (backtest_20261028_232443).
        results_dir (str): Folder of the result files.
        config_path (str): Configuration of the backtest, for the server time zone and the sessions.

    Returns:
        dict: The tables, by name (to use them in tests or in a notebook).
    """
    with open(config_path, "rb") as f:
        props = tomllib.load(f)["strategies"]["market_structure"]
    server_timezone, sessions = props["server_timezone"], [tuple(w) for w in props["sessions"]]

    trades_file = os.path.join(results_dir, f"{name}_trades.csv")
    decisions_file = os.path.join(results_dir, f"{name}_decisions.csv")
    decisions = pd.read_csv(decisions_file) if os.path.exists(decisions_file) else None
    trades = pd.read_csv(trades_file) if os.path.exists(trades_file) else pd.DataFrame()

    print("=" * 78)
    print(f" ANÁLISIS DE {name}")
    print("=" * 78)
    tables = {}
    if trades.empty:
        print("\n Sin operaciones.")
    else:
        trades = prepare(trades, decisions, server_timezone, sessions)
        if trades["r"].isna().any():
            print(f"\n AVISO: {int(trades['r'].isna().sum())} operaciones sin su línea EN-1: su R queda vacío")
        for title, by in [("Por motivo de salida (FRAMEWORK = cierre intradía de las 16:00 NY)", "exit_reason"),
                          ("Por sesión de entrada", "sesión"), ("Por hora de entrada (Nueva York)", "hora NY"),
                          ("Por día de entrada", "día"), ("Por par", "symbol"), ("Por dirección", "side")]:
            tables[by] = summary(trades, by)
            if by == "exit_reason":
                tables[by].loc["TOTAL"] = summary(trades.assign(total="TOTAL"), "total").loc["TOTAL"]
            print_table(title, tables[by])
        tables["trades"] = trades

    if decisions is None:
        print(f"\n No está {decisions_file}: sin embudo de setups")
    else:
        tables["funnel"] = funnel(decisions)
        print_table("Embudo de setups por par", tables["funnel"])

    print(f"\n {len(trades)} operaciones. Con tan pocas, las diferencias entre grupos pueden ser suerte:")
    print(" esto sirve para comprobar que las reglas hacen lo esperado, no para elegir parámetros.")
    return tables


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Análisis de un backtest de market_structure (operaciones y decisiones)")
    parser.add_argument("name", nargs="?", help="Nombre del backtest (backtest_20261028_232443). Por defecto el último")
    parser.add_argument("--results-dir", default="backtest/results")
    parser.add_argument("--config", default="estructura.toml", help="Configuración del backtest (zona horaria y sesiones)")
    args = parser.parse_args()

    pd.set_option("display.width", 200)
    analyze(args.name or latest_results(args.results_dir), args.results_dir, args.config)
