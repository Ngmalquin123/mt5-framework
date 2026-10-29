# Tests de la construcción de M15/H1 a partir de M5 (CV-2) y de las horas de las sesiones (HO-1, HO-2)
from datetime import datetime, timezone
import pandas as pd
import pytest

from market_structure.bars import Bar, BarAggregator, aggregate
from market_structure.sessions import server_to_utc, local_time, in_windows

M5 = 300


def server(year, month, day, hour, minute=0):
    """Hora del servidor tal como la da MT5: el reloj del servidor escrito como si fuera UTC."""
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def m5_bars(start, count):
    return [Bar(start + i * M5, 1.1 + i * 0.0001, 1.1 + i * 0.0001 + 0.0003, 1.1 + i * 0.0001 - 0.0002,
                1.1 + i * 0.0001 + 0.00005, tick_volume=10, spread=i) for i in range(count)]


# ---------------- Velas
def test_h1_bar_is_built_only_when_its_last_m5_bar_closes():
    aggregator = BarAggregator(3600)
    bars = m5_bars(server(2026, 10, 27, 10), 24)
    outputs = [aggregator.update(bar) for bar in bars]

    assert all(out == [] for i, out in enumerate(outputs) if i not in (11, 23))
    (h1,) = outputs[11]
    first_hour = bars[:12]
    assert h1 == Bar(server(2026, 10, 27, 10), first_hour[0].open, max(b.high for b in first_hour),
                     min(b.low for b in first_hour), first_hour[-1].close, 120, first_hour[-1].spread)
    assert outputs[23][0].time == server(2026, 10, 27, 11)


def test_m15_bars():
    result = aggregate(pd.DataFrame(m5_bars(server(2026, 10, 27, 10), 9)), 900)
    assert list(result["time"]) == [server(2026, 10, 27, 10), server(2026, 10, 27, 10, 15), server(2026, 10, 27, 10, 30)]


def test_missing_last_m5_bar_closes_the_period_late_never_early():
    aggregator = BarAggregator(3600)
    bars = m5_bars(server(2026, 10, 27, 10), 13)
    del bars[11]                                    # falta la vela de las 10:55
    outputs = [aggregator.update(bar) for bar in bars]

    assert all(out == [] for out in outputs[:-1])
    (h1,) = outputs[-1]                             # se entrega al llegar la vela de las 11:00
    assert h1.time == server(2026, 10, 27, 10) and h1.tick_volume == 110


def test_incomplete_period_is_not_returned():
    result = aggregate(pd.DataFrame(m5_bars(server(2026, 10, 27, 10), 18)), 3600)
    assert len(result) == 1


# ---------------- Horas
def test_server_time_to_utc_winter_and_summer():
    assert server_to_utc(server(2026, 1, 15, 12), "Europe/Athens") == datetime(2026, 1, 15, 10, tzinfo=timezone.utc)
    assert server_to_utc(server(2026, 7, 15, 12), "Europe/Athens") == datetime(2026, 7, 15, 9, tzinfo=timezone.utc)
    # Servidor con el horario de verano de EE. UU. (Nueva York + 7 h)
    assert server_to_utc(server(2026, 3, 16, 12), "America/New_York+7") == datetime(2026, 3, 16, 9, tzinfo=timezone.utc)


LONDON = ("Europe/London", "08:00", "12:00")
NEW_YORK = ("America/New_York", "08:00", "11:00")


def test_london_window_is_10_to_14_server_time_all_year():
    for month in (1, 7):
        assert not in_windows(server(2026, month, 15, 9, 55), "Europe/Athens", [LONDON])
        assert in_windows(server(2026, month, 15, 10), "Europe/Athens", [LONDON])
        assert in_windows(server(2026, month, 15, 13, 55), "Europe/Athens", [LONDON])
        assert not in_windows(server(2026, month, 15, 14), "Europe/Athens", [LONDON])


def test_new_york_window_moves_when_us_and_europe_change_time_on_different_days():
    # 2026: EE. UU. cambia el 8 de marzo y Europa el 29. Entre medias Nueva York abre una hora antes en el servidor
    assert in_windows(server(2026, 3, 2, 15), "Europe/Athens", [NEW_YORK])          # normal: 15:00 servidor
    assert not in_windows(server(2026, 3, 2, 14, 30), "Europe/Athens", [NEW_YORK])
    assert in_windows(server(2026, 3, 16, 14, 30), "Europe/Athens", [NEW_YORK])     # semana desfasada: 14:00
    assert not in_windows(server(2026, 3, 16, 17), "Europe/Athens", [NEW_YORK])
    assert in_windows(server(2026, 7, 15, 15), "Europe/Athens", [NEW_YORK])


def test_intraday_close_16h_new_york():
    close = local_time(server(2026, 10, 20, 23), "Europe/Athens", "America/New_York")
    assert (close.hour, close.minute) == (16, 0)           # normal: 23:00 del servidor
    close = local_time(server(2026, 10, 27, 22), "Europe/Athens", "America/New_York")
    assert (close.hour, close.minute) == (16, 0)           # 25/10 al 1/11: Europa ya cambió y EE. UU. no → 22:00


@pytest.mark.parametrize("value", ["Mars/Base", "Europe/Athens+x"])
def test_bad_server_timezone_raises(value):
    with pytest.raises(Exception):
        server_to_utc(0, value)


# ---------------- Comprobación de la zona horaria con los datos (backtest/check_data.py)
def test_check_data_detects_the_server_timezone(capsys):
    from zoneinfo import ZoneInfo
    from backtest.check_data import check_server_timezone

    # Datos de un servidor en hora de Atenas: cada semana abre el domingo a las 17:00 de Nueva York
    times = []
    for day in (1, 8, 15, 22, 29):                      # domingos de marzo de 2026 (con la semana desfasada)
        open_utc = datetime(2026, 3, day, 17, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
        athens = open_utc.astimezone(ZoneInfo("Europe/Athens")).replace(tzinfo=timezone.utc)
        times += [int(athens.timestamp()) + i * M5 for i in range(24)]
    df = pd.DataFrame({"time": times})

    assert check_server_timezone(df) == "Europe/Athens"
    out = capsys.readouterr().out
    assert "Europe/Athens            4 de 4" in out
    # «Nueva York + 7» solo coincide la semana del 29: en las tres semanas desfasadas de marzo falla
    assert "America/New_York+7       1 de 4" in out
