# Horas: el servidor de MT5 da las velas en SU hora local. Aquí se convierte a UTC y a la hora de Londres o Nueva York,
# para que las ventanas de sesión respeten los cambios de horario de verano (HO-1, HO-2).
#
# server_timezone es una zona IANA ("Europe/Athens" = UTC+2 en invierno y UTC+3 en verano, con el calendario europeo).
# También acepta "zona+horas" para servidores que siguen el horario de verano de EE. UU.: "America/New_York+7".

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_EPOCH = datetime(1970, 1, 1)


def _parse_server_timezone(server_timezone: str):
    name, _, hours = server_timezone.partition("+")
    return ZoneInfo(name), timedelta(hours=float(hours) if hours else 0)


def server_to_utc(server_time: int, server_timezone: str) -> datetime:
    """
    Converts a server time (seconds, as MT5 gives them) to UTC.

    Args:
        server_time (int): Time of the server in seconds (the clock of the server written as if it were UTC).
        server_timezone (str): Time zone of the server ("Europe/Athens" or "America/New_York+7").

    Returns:
        datetime: The same instant in UTC.
    """
    zone, offset = _parse_server_timezone(server_timezone)
    local = (_EPOCH + timedelta(seconds=server_time) - offset).replace(tzinfo=zone)
    return local.astimezone(timezone.utc)


def local_time(server_time: int, server_timezone: str, timezone_name: str) -> datetime:
    """Converts a server time to the local time of another zone (for example Europe/London)."""
    return server_to_utc(server_time, server_timezone).astimezone(ZoneInfo(timezone_name))


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def in_windows(server_time: int, server_timezone: str, windows: list) -> bool:
    """
    Checks if a server time is inside any of the windows (HO-2).

    Args:
        server_time (int): Time of the server in seconds.
        server_timezone (str): Time zone of the server.
        windows (list): (time zone, "HH:MM" start, "HH:MM" end) of each window, in local time. The end is not included.

    Returns:
        bool: True if it is inside one window.
    """
    for timezone_name, start, end in windows:
        now = local_time(server_time, server_timezone, timezone_name).time()
        if parse_hhmm(start) <= now < parse_hhmm(end):
            return True
    return False
