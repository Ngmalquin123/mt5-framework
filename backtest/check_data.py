# Comprobaciones con los datos descargados del broker (Fase 2):
#   1) Zona horaria del servidor (HO-1): el mercado abre cada semana el domingo a las 17:00 de Nueva York.
#      La hora de la primera vela de cada semana dice qué zona horaria sigue el servidor.
#   2) Velas M15 y H1 construidas a partir de M5 (CV-2): deben coincidir con las que descarga MT5.
#
# Uso (después de descargar M5, M15 y H1 con backtest.download_data):
#   uv run python -m backtest.check_data --symbols EURUSD GBPUSD

from market_structure.bars import aggregate
from market_structure.sessions import local_time
from datetime import datetime, timedelta, timezone
import argparse
import os
import pandas as pd

CANDIDATE_TIMEZONES = ["Europe/Athens", "America/New_York+7", "Europe/London", "UTC"]


def weekly_first_bars(times: pd.Series) -> list:
    """Hora de la primera vela de cada semana: la que llega después de un hueco de más de 24 horas."""
    gaps = times.diff() > 24 * 3600
    return [int(t) for t in times[gaps]]


def opens_on_sunday_new_york(server_time: int, server_timezone: str) -> bool:
    ny = local_time(server_time, server_timezone, "America/New_York")
    return ny.weekday() == 6 and 17 <= ny.hour < 18


def check_server_timezone(df: pd.DataFrame) -> str:
    firsts = weekly_first_bars(df["time"])
    if not firsts:
        print("  No hay fines de semana en los datos: no se puede comprobar")
        return None

    print(f"  {len(firsts)} semanas. Primera vela de cada semana que coincide con el domingo 17:00 de Nueva York:")
    scores = {tz: sum(opens_on_sunday_new_york(t, tz) for t in firsts) for tz in CANDIDATE_TIMEZONES}
    for tz, score in scores.items():
        print(f"    {tz:<22} {score:>3} de {len(firsts)}")

    # Semanas de marzo y de octubre/noviembre: es donde se distinguen el horario europeo y el de EE. UU.
    print("  Semanas de cambio de hora (hora del servidor de la primera vela):")
    for t in firsts:
        server = datetime(1970, 1, 1) + timedelta(seconds=t)
        if server.month in (3, 4, 10, 11):
            print(f"    {server:%Y-%m-%d %a %H:%M}")

    best = max(scores, key=scores.get)
    print(f"  → server_timezone más probable: {best}")
    return best


def check_aggregation(m5: pd.DataFrame, other: pd.DataFrame, timeframe_seconds: int, name: str) -> None:
    built = aggregate(m5, timeframe_seconds)
    merged = built.merge(other, on="time", suffixes=("_m5", "_mt5"))
    if merged.empty:
        print(f"  {name}: no hay velas en común (los periodos descargados no se solapan)")
        return
    same = sum(all(abs(row[f"{c}_m5"] - row[f"{c}_mt5"]) < 1e-9 for c in ("open", "high", "low", "close"))
               for _, row in merged.iterrows())
    print(f"  {name}: {same} de {len(merged)} velas iguales ({same / len(merged):.2%})")
    if same < len(merged):
        diff = merged[[not all(abs(row[f"{c}_m5"] - row[f"{c}_mt5"]) < 1e-9 for c in ("open", "high", "low", "close"))
                       for _, row in merged.iterrows()]]
        for _, row in diff.head(3).iterrows():
            print(f"    distinta: {datetime.fromtimestamp(int(row['time']), tz=timezone.utc):%Y-%m-%d %H:%M} "
                  f"M5→ {row['open_m5']}/{row['high_m5']}/{row['low_m5']}/{row['close_m5']} "
                  f"MT5→ {row['open_mt5']}/{row['high_mt5']}/{row['low_mt5']}/{row['close_mt5']}")


def main(symbols: list, data_dir: str) -> None:
    for symbol in symbols:
        print(f"\n{symbol}")
        path = os.path.join(data_dir, f"{symbol}_5min.csv")
        if not os.path.exists(path):
            print(f"  Falta {path}. Descárgalo con: uv run python -m backtest.download_data --symbols {symbol} --timeframe 5min --start 2025-01-01")
            continue
        m5 = pd.read_csv(path)
        print("1) Zona horaria del servidor")
        check_server_timezone(m5)
        print("2) Velas construidas a partir de M5")
        for timeframe, seconds in (("15min", 900), ("1h", 3600)):
            other_path = os.path.join(data_dir, f"{symbol}_{timeframe}.csv")
            if os.path.exists(other_path):
                check_aggregation(m5, pd.read_csv(other_path), seconds, timeframe)
            else:
                print(f"  {timeframe}: falta {other_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Comprueba la zona horaria del servidor y las velas construidas a partir de M5")
    parser.add_argument("--symbols", nargs="+", default=["EURUSD"])
    parser.add_argument("--data-dir", default="backtest/data")
    args = parser.parse_args()
    main(args.symbols, args.data_dir)
