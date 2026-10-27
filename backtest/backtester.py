# Motor del backtest: mueve el MT5 simulado vela a vela y pasa los eventos por los mismos módulos que en real

from backtest.simulated_mt5 import SimulatedMT5
from contextlib import redirect_stdout
from datetime import datetime, timezone
import csv
import io
import os
import sys


class Backtester():

    def __init__(self, simulated_mt5: SimulatedMT5, trading_director, trade_journal, results_dir: str = "backtest/results",
                verbose: bool = False):
        """
        Initializes the Backtester object.

        Args:
            simulated_mt5 (SimulatedMT5): The simulated broker, already activated.
            trading_director (TradingDirector): The trading director with all the modules of the strategy.
            trade_journal (TradeJournal): Journal where the closed trades of the backtest are saved.
            results_dir (str): Folder where the equity curve is saved.
            verbose (bool): If True, shows all the messages of the modules (very slow with many bars).
        """
        self.SIM = simulated_mt5
        self.DIRECTOR = trading_director
        self.TRADE_JOURNAL = trade_journal
        self.results_dir = results_dir
        self.verbose = verbose

    def _process_events(self) -> None:
        """Processes all the events in the queue with the handlers of the trading director."""
        events_queue = self.DIRECTOR.events_queue
        while not events_queue.empty():
            event = events_queue.get()
            if event is None:
                self.DIRECTOR._handle_none_event(event)
                return
            handler = self.DIRECTOR.event_handler.get(event.event_type, self.DIRECTOR._handle_unknown_event)
            handler(event)

    def _run_loop(self, progress_output) -> None:
        total = len(self.SIM.timeline)
        next_progress = 0.1

        while self.DIRECTOR.continue_trading and self.SIM.step():
            # Mismo orden que el bucle real: datos nuevos -> eventos -> tareas periódicas
            self.DIRECTOR.DATA_PROVIDER.check_for_new_data()
            self._process_events()
            self.DIRECTOR.ORDER_EXECUTOR.cancel_expired_pending_orders()
            self.SIM.record_equity()

            if self.SIM.equity_curve[-1][2] <= 0:
                print("BACKTEST: La cuenta se ha quedado sin equity. Fin del backtest", file=progress_output)
                break

            if (self.SIM.step_index + 1) / total >= next_progress:
                print(f"BACKTEST: {next_progress:.0%} completado ({self._format_time(self.SIM.now)})", file=progress_output)
                next_progress += 0.1

        # Al terminar cerramos lo que quede abierto al último precio
        self.SIM.close_all_positions()
        self.SIM.record_equity()

    def run(self) -> dict:
        """
        Runs the backtest from the first to the last bar of the simulated broker.

        Returns:
            dict: The statistics of the backtest.
        """
        progress_output = sys.stdout
        print(f"BACKTEST: {len(self.SIM.timeline)} velas de {self.SIM.timeframe} desde {self._format_time(self.SIM.timeline[0])} "
              f"hasta {self._format_time(self.SIM.timeline[-1])}")

        if self.verbose:
            self._run_loop(progress_output)
        else:
            # Los mensajes de cada módulo se descartan: con miles de velas ralentizan mucho el backtest
            with redirect_stdout(io.StringIO()):
                self._run_loop(progress_output)

        with redirect_stdout(io.StringIO()):
            trades = self.TRADE_JOURNAL.update(0, sys.maxsize)

        stats = self.compute_stats(trades, self.SIM.equity_curve, self.SIM.initial_balance)
        self._save_equity_curve()
        self.print_report(stats)
        return stats

    @staticmethod
    def _format_time(timestamp) -> str:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def compute_stats(trades: list, equity_curve: list, initial_balance: float) -> dict:
        """
        Computes the statistics of the backtest.

        Args:
            trades (list): Closed trades (rows of the trade journal).
            equity_curve (list): (time, balance, equity) of each bar.
            initial_balance (float): Initial balance of the account.

        Returns:
            dict: The statistics.
        """
        results = [float(t["net_profit"]) for t in trades]
        wins = [r for r in results if r > 0]
        losses = [r for r in results if r <= 0]
        gross_profit = sum(wins)
        gross_loss = -sum(losses)

        # Máximo drawdown sobre la equity (incluye las pérdidas flotantes)
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        peak = initial_balance
        for _, _, equity in equity_curve:
            peak = max(peak, equity)
            drawdown = peak - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_pct = drawdown / peak if peak > 0 else 0.0

        final_balance = equity_curve[-1][1] if equity_curve else initial_balance

        by_symbol = {}
        for trade in trades:
            symbol_stats = by_symbol.setdefault(trade["symbol"], {"trades": 0, "net_profit": 0.0})
            symbol_stats["trades"] += 1
            symbol_stats["net_profit"] = round(symbol_stats["net_profit"] + float(trade["net_profit"]), 2)

        return {
            "initial_balance": initial_balance,
            "final_balance": round(final_balance, 2),
            "net_profit": round(sum(results), 2),
            "return_pct": (final_balance - initial_balance) / initial_balance if initial_balance else 0.0,
            "trades": len(results),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(results) if results else 0.0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
            "avg_win": gross_profit / len(wins) if wins else 0.0,
            "avg_loss": -gross_loss / len(losses) if losses else 0.0,
            "best_trade": max(results) if results else 0.0,
            "worst_trade": min(results) if results else 0.0,
            "max_drawdown": round(max_drawdown, 2),
            "max_drawdown_pct": max_drawdown_pct,
            "by_symbol": by_symbol,
        }

    def _save_equity_curve(self) -> None:
        os.makedirs(self.results_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(self.TRADE_JOURNAL.file_path))[0].replace("_trades", "")
        self.equity_file = os.path.join(self.results_dir, f"{base_name}_equity.csv")

        with open(self.equity_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "balance", "equity"])
            for timestamp, balance, equity in self.SIM.equity_curve:
                writer.writerow([self._format_time(timestamp), round(balance, 2), equity])

    def print_report(self, stats: dict) -> None:
        ccy = self.SIM.account_currency
        print("")
        print("=" * 60)
        print(" RESULTADO DEL BACKTEST")
        print("=" * 60)
        print(f" Balance inicial:     {stats['initial_balance']:>12.2f} {ccy}")
        print(f" Balance final:       {stats['final_balance']:>12.2f} {ccy}")
        print(f" Beneficio neto:      {stats['net_profit']:>+12.2f} {ccy} ({stats['return_pct']:+.2%})")
        print(f" Máximo drawdown:     {stats['max_drawdown']:>12.2f} {ccy} ({stats['max_drawdown_pct']:.2%})")
        print("-" * 60)
        print(f" Operaciones:         {stats['trades']:>12} ({stats['wins']} ganadoras, {stats['losses']} perdedoras)")
        print(f" % acierto:           {stats['win_rate']:>12.2%}")
        print(f" Profit factor:       {stats['profit_factor']:>12.2f}")
        print(f" Media ganadora:      {stats['avg_win']:>+12.2f} {ccy}")
        print(f" Media perdedora:     {stats['avg_loss']:>+12.2f} {ccy}")
        print(f" Mejor / peor:        {stats['best_trade']:>+12.2f} / {stats['worst_trade']:+.2f} {ccy}")
        if stats["by_symbol"]:
            print("-" * 60)
            for symbol, symbol_stats in stats["by_symbol"].items():
                print(f" {symbol:<20} {symbol_stats['trades']:>5} operaciones {symbol_stats['net_profit']:>+12.2f} {ccy}")
        print("=" * 60)
        print(f" Operaciones: {self.TRADE_JOURNAL.file_path}")
        print(f" Curva de equity: {self.equity_file}")
        print("")
