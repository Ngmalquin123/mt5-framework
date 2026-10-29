# Estructura de mercado sin indicadores: tendencia en H1, ruptura y retroceso en M15, confirmación y entrada en M5.
# La máquina de estados de cada símbolo está en market_structure/state_machine.py (sin MT5). Aquí se le pasan las velas
# M5 nuevas y se aplican a su señal los filtros de la especificación: horario (HO), una posición en total (RG-2),
# vigencia (EN-2), SL (SL-1, SL-2) y espacio hasta el TP (TP-1, TP-2). Cada descarte se registra con su motivo.
#
# Fase 3: sin filtro de noticias (llega en la Fase 4). El informe del backtest lo indica.

from events.events import DataEvent, SignalEvent
from data_provider.data_provider import DataProvider
from ..interfaces.signal_generator_interface import ISignalGenerator
from ..properties.signal_generator_properties import MarketStructureProps
from market_structure.bars import Bar
from market_structure.sessions import in_windows, local_time, parse_hhmm, server_to_utc
from market_structure.state_machine import SetupMachine, SetupParams, Signal, SENAL
from portfolio.portfolio import Portfolio
from order_executor.order_executor import OrderExecutor
from utils.utils import Utils
from collections import Counter
from datetime import datetime, timezone
from typing import Dict
from zoneinfo import ZoneInfo
import csv
import os
import MetaTrader5 as mt5

NEWS_NOTE = "Filtro de noticias: DESACTIVADO. No hay un histórico real de noticias, así que no se tienen en cuenta (NW-7)"


class SignalMarketStructure(ISignalGenerator):

    def __init__(self, properties: MarketStructureProps):
        """
        Initializes the market structure strategy.

        Args:
            properties (MarketStructureProps): Parameters of the strategy (section 13 of the specification).

        Raises:
            Exception: If the timeframe is not 5min or a time zone or an hour is not valid.
        """
        if properties.timeframe != "5min":
            raise Exception(f"ERROR: market_structure necesita timeframe = \"5min\" (construye M15 y H1 a partir de M5), no {properties.timeframe}")

        # Zonas horarias y horas: un error de escritura debe fallar al arrancar, no en la primera señal
        server_to_utc(0, properties.server_timezone)
        for zone, start, end in properties.sessions:
            ZoneInfo(zone)
            parse_hhmm(start), parse_hhmm(end)
        self.intraday_close = parse_hhmm(properties.intraday_close)
        self.friday_entry_end = parse_hhmm(properties.friday_entry_end)

        self.props = properties
        self.params = SetupParams(properties.swing_n_h1, properties.swing_n_m15, properties.swing_n_m5, properties.break_margin_points,
                                  properties.zone_tol_points, properties.retrace_max_m15, properties.confirm_max_m5,
                                  properties.break_kinds, properties.confirmation)
        self.warmup_bars = properties.warmup_days * 288

        self.machines: Dict[str, SetupMachine] = {}
        self.last_bar_time: Dict[str, int] = {}
        self.last_signal_time = None        # RG-2: dos pares con señal en la misma vela → gana el primero de symbols
        self.sent_time: Dict[str, int] = {}     # hora de la última señal enviada de cada símbolo
        self.account_checked = False

    # ---------------- Señal
    def generate_signal(self, data_event: DataEvent, data_provider: DataProvider, portfolio: Portfolio, order_executor: OrderExecutor) -> SignalEvent:
        """
        Processes the new M5 bars of the symbol and returns a signal if the setup is complete and passes the filters.

        Args:
            data_event (DataEvent): The data event that triggered the signal generation.
            data_provider (DataProvider): The data provider used to retrieve the bars and the tick.
            portfolio (Portfolio): The portfolio containing the open positions.
            order_executor (OrderExecutor): The order executor, to close the positions at the intraday close.

        Returns:
            SignalEvent: The signal event, or None.
        """
        symbol = data_event.symbol
        if not self.account_checked:
            self._check_account()

        tick = data_provider.get_latest_tick(symbol)
        if not tick:
            return None
        now = int(tick['time'])

        # HO-3: cierre intradía. Lo que siga abierto se cierra a las 16:00 de Nueva York
        if self._own_positions(portfolio, symbol) and self._after_intraday_close(now):
            print(f"{Utils.dateprint()} - SIGNAL: Cierre intradía ({self.props.intraday_close} de Nueva York). Cerrando posiciones de {symbol}")
            order_executor.close_strategy_long_positions_by_symbol(symbol)
            order_executor.close_strategy_short_positions_by_symbol(symbol)

        machine = self._machine(symbol)
        has_position = bool(self._own_positions(portfolio, symbol))
        # Una posición que se abre y se cierra dentro de la misma vela M5 no se llega a ver abierta: se busca en el historial
        opened_and_closed = (not has_position and machine.state == SENAL
                             and self._opened_since(symbol, portfolio.magic, self.sent_time.get(symbol, now), now))
        machine.sync_position(now, has_position, opened_and_closed)

        # Velas M5 nuevas. Al arrancar se leen warmup_days de historial; una señal de una vela que no es la última no vale
        bars = self._new_bars(symbol, data_provider, now)
        signal = None
        for i, bar in enumerate(bars):
            signal = machine.update(bar)
            if signal is not None and i < len(bars) - 1:
                machine.discard_signal(signal.close_time, "EN-2: señal de una vela antigua (arranque o reconexión)")
                signal = None

        if signal is None:
            return None
        return self._check_signal(symbol, machine, signal, tick, now, portfolio)

    def _check_signal(self, symbol: str, machine: SetupMachine, signal: Signal, tick: dict, now: int, portfolio: Portfolio) -> SignalEvent:
        info = mt5.symbol_info(symbol)
        point, digits = info.point, info.digits
        spread_points = info.spread
        is_buy = signal.direction == "BUY"
        ny = local_time(now, self.props.server_timezone, "America/New_York")

        reason = None
        if not in_windows(now, self.props.server_timezone, self.props.sessions):
            reason = "HO-2: señal fuera de las ventanas de entrada"
        elif self._after_intraday_close(now):
            reason = "HO-3: señal después del cierre intradía"
        elif ny.weekday() == 4 and ny.time() >= self.friday_entry_end:
            reason = f"HO-3: viernes después de las {self.props.friday_entry_end} de Nueva York"
        elif portfolio.get_strategy_open_positions() or self.last_signal_time == now:
            reason = "RG-2: ya hay una posición abierta (o una señal) en otro par"
        elif abs(tick['bid'] - signal.close) > self.props.max_entry_slip_points * point + point / 2:
            reason = f"EN-2: el precio se ha alejado {abs(tick['bid'] - signal.close) / point:.0f} puntos del cierre de la señal"

        if reason is None:
            # SL-1: detrás del HL de M5 (compras) o del LH más el spread (ventas, que se cierran al Ask).
            # V1: detrás del origen de la ruptura M15
            entry = tick['ask'] if is_buy else tick['bid']
            stop = signal.origin if self.props.stop_mode == "m15_origin" else signal.stop_swing
            if is_buy:
                sl = round(stop - self.props.sl_buffer_points * point, digits)
            else:
                sl = round(stop + self.props.sl_buffer_points * point + spread_points * point, digits)
            distance_points = round(abs(entry - sl) / point)
            min_points = max(self.props.min_sl_points, 3 * spread_points, info.trade_stops_level)

            if (entry - sl if is_buy else sl - entry) <= 0:
                reason = "SL-1: el precio ya está al otro lado del SL"
            elif distance_points < min_points:
                reason = f"SL-2: SL de {distance_points} puntos, menor que el mínimo de {min_points:g} (5 pips, 3 × spread o stops level)"
            elif distance_points > self.props.max_sl_points:
                reason = f"SL-2: SL de {distance_points} puntos, mayor que el máximo de {self.props.max_sl_points:g}"
            else:
                # TP-1 y TP-2: el swing contrario de H1 más cercano (confirmado y no roto) tiene que estar más allá del TP
                tp = round(entry + self.props.rr * distance_points * point if is_buy else entry - self.props.rr * distance_points * point, digits)
                obstacle = self._h1_obstacle(machine, entry, is_buy)
                if obstacle is not None and (obstacle < tp if is_buy else obstacle > tp):
                    reason = f"TP-2: swing {'high' if is_buy else 'low'} de H1 en {obstacle:.{digits}f} antes del TP {tp:.{digits}f}"

        if reason is not None:
            machine.discard_signal(now, reason)
            return None

        self.last_signal_time = now
        self.sent_time[symbol] = now
        machine.log.append((now, SENAL, SENAL, f"EN-1: {signal.direction} a {entry:.{digits}f}, SL {sl:.{digits}f}, TP {tp:.{digits}f}"))
        print(f"{Utils.dateprint()} - SIGNAL: {signal.direction} {symbol} a {entry:.{digits}f} | SL {sl:.{digits}f} | TP {tp:.{digits}f} "
              f"(ruptura M15 {signal.level:.{digits}f}, HL/LH M5 {signal.stop_swing:.{digits}f})")

        return SignalEvent(symbol=symbol,
                           signal=signal.direction,
                           target_order="MARKET",
                           target_price=0.0,
                           magic_number=portfolio.magic,
                           sl=sl,
                           tp=tp)

    @staticmethod
    def _h1_obstacle(machine: SetupMachine, entry: float, is_buy: bool):
        if is_buy:
            prices = [s.price for s in machine.h1.unbroken_swings("high") if s.price > entry]
            return min(prices) if prices else None
        prices = [s.price for s in machine.h1.unbroken_swings("low") if s.price < entry]
        return max(prices) if prices else None

    # ---------------- Auxiliares
    def _check_account(self) -> None:
        # LIVE nunca se activa solo: esta estrategia se niega a funcionar en una cuenta real
        account = mt5.account_info()
        if account is not None and account.trade_mode == getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2):
            raise Exception("ERROR: market_structure está en pruebas y no puede operar en una cuenta REAL (modo LIVE desactivado)")
        self.account_checked = True

    def _machine(self, symbol: str) -> SetupMachine:
        if symbol not in self.machines:
            self.machines[symbol] = SetupMachine(self.params, mt5.symbol_info(symbol).point)
        return self.machines[symbol]

    @staticmethod
    def _own_positions(portfolio: Portfolio, symbol: str) -> list:
        return [p for p in portfolio.get_strategy_open_positions() if p.symbol == symbol]

    @staticmethod
    def _opened_since(symbol: str, magic: int, since: int, now: int) -> bool:
        deals = mt5.history_deals_get(since, now + 60) or ()
        return any(d.symbol == symbol and d.magic == magic and d.entry == mt5.DEAL_ENTRY_IN for d in deals)

    def _after_intraday_close(self, now: int) -> bool:
        return local_time(now, self.props.server_timezone, "America/New_York").time() >= self.intraday_close

    def _new_bars(self, symbol: str, data_provider: DataProvider, now: int) -> list:
        last = self.last_bar_time.get(symbol)
        count = self.warmup_bars if last is None else min(self.warmup_bars, max(3, (now - last) // 300 + 3))
        # Directamente de MT5 (sin DataFrame): se llama en cada vela de cada símbolo y en el backtest son cientos de miles
        # de llamadas. Posición 1 = la última vela cerrada, igual que get_latest_closed_bars
        rates = mt5.copy_rates_from_pos(symbol, data_provider._map_timeframes("5min"), 1, count)
        if rates is None or len(rates) == 0:
            return []

        bars = [Bar(int(r['time']), float(r['open']), float(r['high']), float(r['low']), float(r['close']), int(r['tick_volume']),
                    int(r['spread'])) for r in rates if last is None or r['time'] > last]
        self.last_bar_time[symbol] = int(rates['time'][-1])
        return bars

    # ---------------- Registro de decisiones
    def decision_log(self) -> list:
        """Every change of state of every symbol, with its reason, in order of time."""
        rows = []
        for symbol, machine in self.machines.items():
            for time, old, new, reason in machine.log:
                rows.append({"time": datetime.fromtimestamp(time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"), "symbol": symbol,
                             "from": old, "to": new, "reason": reason})
        return sorted(rows, key=lambda row: row["time"])

    def save_decision_log(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "symbol", "from", "to", "reason"])
            writer.writeheader()
            writer.writerows(self.decision_log())
        return path

    def report_lines(self) -> list:
        """Summary of the setups for the backtest report: how many reached each step and why the others stopped."""
        rules = Counter(row["reason"].split(":")[0] for row in self.decision_log())
        stops = ", ".join(f"{rule} {rules[rule]}" for rule in ("ST-5", "ST-6", "ST-10", "HO-2", "HO-3", "RG-2", "EN-2", "SL-1", "SL-2", "TP-2", "EN-3/EN-4")
                          if rules[rule])
        return [f" Rupturas M15: {rules['ST-2'] + rules['ST-6 (reemplazo)']} | toques de la zona: {rules['ST-4']} | "
                f"señales: {rules['ST-8']} | enviadas: {rules['EN-1']} | ejecutadas: {rules['EN-4']}",
                f" Paradas: {stops or 'ninguna'}",
                f" {NEWS_NOTE}"]
