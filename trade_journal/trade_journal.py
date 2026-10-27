# Diario de operaciones: guarda en un CSV cada operación cerrada por la estrategia (entrada, salida y resultado)

from utils.utils import Utils
from datetime import datetime, date, timezone
import MetaTrader5 as mt5
import csv
import os
import time


class TradeJournal():

    COLUMNS = ["exit_time", "symbol", "side", "volume", "entry_time", "entry_price", "exit_price", "exit_reason",
               "profit", "commission", "swap", "net_profit", "strategy", "magic", "position_id", "exit_deal"]

    def __init__(self, magic_number: int, strategy_name: str, file_path: str = "logs/trades.csv", lookback_days: int = 7):
        """
        Initializes the TradeJournal object.

        Args:
            magic_number (int): Only the trades of this magic number are recorded.
            strategy_name (str): Name of the strategy, saved in each row.
            file_path (str): CSV file where the trades are saved. It can be opened with Excel.
            lookback_days (int): Days of history checked in each update (to record trades closed while the Framework was off).
        """
        self.magic = magic_number
        self.strategy_name = strategy_name
        self.file_path = file_path
        self.lookback_days = lookback_days

        # Deals de cierre ya guardados (para no repetir operaciones al reiniciar el Framework)
        self.recorded_deals: set = {int(row["exit_deal"]) for row in self.get_trades()}

    @staticmethod
    def _format_time(timestamp: int) -> str:
        # El time de los deals de MT5 está en hora del servidor
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _exit_reason(reason: int, comment: str = "") -> str:
        # Cierres del backtest al llegar al final de los datos
        if comment == "FIN BACKTEST":
            return comment
        reasons = {mt5.DEAL_REASON_SL: "SL", mt5.DEAL_REASON_TP: "TP", mt5.DEAL_REASON_SO: "STOP OUT", mt5.DEAL_REASON_EXPERT: "FRAMEWORK"}
        return reasons.get(reason, "MANUAL")

    def get_trades(self) -> list:
        """
        Returns all the trades saved in the CSV file.

        Returns:
            list: A list of dicts, one per trade.
        """
        if not os.path.exists(self.file_path):
            return []

        with open(self.file_path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _build_trade(self, exit_deal) -> dict:
        """
        Builds the trade row from its exit deal and the entry deal of the same position.
        """
        entry_deals = [d for d in mt5.history_deals_get(position=exit_deal.position_id) or () if d.entry == mt5.DEAL_ENTRY_IN]
        entry_deal = entry_deals[0] if entry_deals else None

        # El deal de salida va en sentido contrario a la posición (cerrar una compra es vender)
        side = "SELL" if exit_deal.type == mt5.DEAL_TYPE_BUY else "BUY"
        commission = exit_deal.commission + (entry_deal.commission if entry_deal else 0.0)
        swap = exit_deal.swap

        return {
            "exit_time": self._format_time(exit_deal.time),
            "symbol": exit_deal.symbol,
            "side": side,
            "volume": exit_deal.volume,
            "entry_time": self._format_time(entry_deal.time) if entry_deal else "",
            "entry_price": entry_deal.price if entry_deal else "",
            "exit_price": exit_deal.price,
            "exit_reason": self._exit_reason(exit_deal.reason, exit_deal.comment),
            "profit": round(exit_deal.profit, 2),
            "commission": round(commission, 2),
            "swap": round(swap, 2),
            "net_profit": round(exit_deal.profit + commission + swap, 2),
            "strategy": self.strategy_name,
            "magic": exit_deal.magic,
            "position_id": exit_deal.position_id,
            "exit_deal": exit_deal.ticket,
        }

    def update(self, date_from=None, date_to=None) -> list:
        """
        Looks for trades closed by the strategy that are not in the journal yet and saves them.

        Args:
            date_from (optional): Start of the history to check. By default, lookback_days ago.
            date_to (optional): End of the history to check. By default, a couple of days from now (server time can be ahead).

        Returns:
            list: The new trades (dicts) saved in this update.
        """
        if date_from is None:
            date_from = int(time.time()) - self.lookback_days * 86400
        if date_to is None:
            date_to = int(time.time()) + 2 * 86400

        deals = mt5.history_deals_get(date_from, date_to) or ()
        exit_deals = [d for d in deals
                      if d.magic == self.magic and d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY) and d.ticket not in self.recorded_deals]

        new_trades = [self._build_trade(deal) for deal in sorted(exit_deals, key=lambda d: d.time_msc)]
        if not new_trades:
            return []

        folder = os.path.dirname(self.file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        write_header = not os.path.exists(self.file_path)

        with open(self.file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_trades)

        self.recorded_deals.update(trade["exit_deal"] for trade in new_trades)
        print(f"{Utils.dateprint()} - JOURNAL: {len(new_trades)} operación(es) cerrada(s) guardada(s) en {self.file_path}")
        return new_trades

    def daily_summary(self, day: date) -> str:
        """
        Builds a text summary of the trades closed on the given day (server time).

        Args:
            day (date): The day to summarize.

        Returns:
            str: The summary, ready to be sent by Telegram.
        """
        day_str = day.strftime("%Y-%m-%d")
        trades = [t for t in self.get_trades() if t["exit_time"].startswith(day_str)]
        account_info = mt5.account_info()
        ccy = account_info.currency if account_info else ""

        lines = [f"Resumen del {day:%d/%m/%Y} (hora servidor) - {self.strategy_name}"]
        if not trades:
            lines.append("Sin operaciones cerradas")
        else:
            results = [float(t["net_profit"]) for t in trades]
            wins = sum(1 for r in results if r > 0)
            lines.append(f"Operaciones: {len(trades)} ({wins} ganadoras, {len(trades) - wins} perdedoras)")
            lines.append(f"Resultado: {sum(results):+.2f} {ccy}")
            lines.append(f"Mejor: {max(results):+.2f} | Peor: {min(results):+.2f}")

        if account_info:
            lines.append(f"Balance: {account_info.balance:.2f} {ccy} | Equity: {account_info.equity:.2f} {ccy}")

        return "\n".join(lines)
