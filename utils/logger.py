import os
import sys
from datetime import datetime


class _DailyLogFile():
    """
    Log file shared by stdout and stderr. A new file is opened every day inside the logs folder.
    """

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._current_date = None
        self._file = None

    def write(self, message: str) -> None:
        # Abrimos un archivo nuevo cuando cambia el día
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file is not None:
                self._file.close()
            self._file = open(os.path.join(self.log_dir, f"trading_{today}.log"), "a", encoding="utf-8")
            self._current_date = today

        self._file.write(message)
        self._file.flush()


class TeeLogger():
    """
    Writes everything printed to the console to the console AND to the daily log file.
    """

    def __init__(self, stream, log_file: _DailyLogFile):
        self.stream = stream
        self.log_file = log_file

    def write(self, message: str) -> None:
        self.stream.write(message)
        self.log_file.write(message)

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        return self.stream.isatty()

    @staticmethod
    def start(log_dir: str = "logs") -> None:
        """
        Starts duplicating stdout and stderr (prints, errors and tracebacks) into logs/trading_YYYY-MM-DD.log
        """
        log_file = _DailyLogFile(log_dir)
        sys.stdout = TeeLogger(sys.stdout, log_file)
        sys.stderr = TeeLogger(sys.stderr, log_file)
