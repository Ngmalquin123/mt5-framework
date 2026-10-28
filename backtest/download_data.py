# Descarga velas históricas de MT5 a CSV para el backtest. Necesita Windows con MT5 abierto y el .env configurado.
#
# Uso (desde la carpeta del proyecto):
#   uv run python -m backtest.download_data --symbols EURUSD USDJPY GBPUSD --timeframe 1h --start 2025-01-01 --end 2026-10-01

from platform_connector.platform_connector import PlatformConnector
from data_provider.data_provider import DataProvider
from backtest.simulated_mt5 import SYMBOL_SPEC_FIELDS
from dotenv import load_dotenv, find_dotenv
from datetime import datetime, timedelta, timezone
from queue import Queue
import MetaTrader5 as mt5
import pandas as pd
import argparse
import json
import os


def download_symbol_rates(symbol: str, tf: int, date_from: datetime, date_to: datetime, chunk_days: int = 30) -> pd.DataFrame:
    """
    Downloads the bars of a symbol month by month and joins them.
    If the whole period is asked at once, MT5 answers (-2, 'Invalid params') when there are more bars
    than 'Máx. barras en el gráfico' (e.g. M5 or M1 for more than a year).

    Args:
        symbol (str): Symbol to download.
        tf (int): MT5 timeframe.
        date_from (datetime): First date (UTC).
        date_to (datetime): Last date (UTC).
        chunk_days (int): Days of each request.

    Returns:
        pd.DataFrame: The bars, sorted and without duplicates. Empty if nothing could be downloaded.
    """
    chunks = []
    chunk_start = date_from
    while chunk_start < date_to:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), date_to)
        rates = mt5.copy_rates_range(symbol, tf, chunk_start, chunk_end)
        if rates is not None and len(rates) > 0:
            chunks.append(pd.DataFrame(rates))
        chunk_start = chunk_end

    if not chunks:
        return pd.DataFrame()

    # Las velas de los bordes de cada trozo salen repetidas
    return pd.concat(chunks).drop_duplicates('time').sort_values('time').reset_index(drop=True)


def download(symbols: list, timeframe: str, start: str, end: str, data_dir: str = "backtest/data") -> None:
    """
    Downloads the bars of each symbol and its specifications (digits, contract size...) to data_dir.

    Args:
        symbols (list): Symbols to download.
        timeframe (str): Timeframe ('1min', '1h'...).
        start (str): First date ("YYYY-MM-DD").
        end (str): Last date ("YYYY-MM-DD").
        data_dir (str): Destination folder.
    """
    load_dotenv(find_dotenv())
    PlatformConnector(symbol_list=symbols)
    tf = DataProvider(Queue(), symbols, timeframe)._map_timeframes(timeframe)

    date_from = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_to = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    os.makedirs(data_dir, exist_ok=True)

    for symbol in symbols:
        print(f"{symbol}: descargando {timeframe} desde {start}...", flush=True)
        bars = download_symbol_rates(symbol, tf, date_from, date_to)
        if bars.empty:
            print(f"ERROR: No se han podido descargar velas de {symbol}. MT5 Error: {mt5.last_error()}")
            continue

        bars.to_csv(os.path.join(data_dir, f"{symbol}_{timeframe}.csv"), index=False)

        first_bar = datetime.fromtimestamp(int(bars['time'].iloc[0]), tz=timezone.utc)
        print(f"{symbol}: {len(bars)} velas desde {first_bar:%Y-%m-%d %H:%M} hasta "
              f"{datetime.fromtimestamp(int(bars['time'].iloc[-1]), tz=timezone.utc):%Y-%m-%d %H:%M}")

        # MT5 solo da tantas velas como "Máx. barras en el gráfico" (Herramientas > Opciones > Gráficos)
        if (first_bar - date_from).days > 3:
            print(f"  AVISO: {symbol} empieza más tarde de lo pedido: el servidor no tiene historial anterior de {timeframe}")

        info = mt5.symbol_info(symbol)
        with open(os.path.join(data_dir, f"{symbol}.json"), "w", encoding="utf-8") as f:
            json.dump({field: getattr(info, field) for field in SYMBOL_SPEC_FIELDS}, f, indent=2)

    mt5.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga velas de MT5 para el backtest")
    parser.add_argument("--symbols", nargs="+", required=True, help="Símbolos, p.ej. EURUSD USDJPY GBPUSD")
    parser.add_argument("--timeframe", default="1h", help="Timeframe: 1min, 5min, 15min, 1h, 4h, 1d...")
    parser.add_argument("--start", required=True, help="Fecha inicial YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="Fecha final YYYY-MM-DD (por defecto hoy)")
    args = parser.parse_args()

    download(args.symbols, args.timeframe, args.start, args.end)
