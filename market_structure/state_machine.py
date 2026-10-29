# Máquina de estados de la estrategia market_structure para UN símbolo (sección 10 de la especificación).
#
# Recibe solo velas M5 cerradas, construye con ellas las de M15 y H1 (CV-2) y sigue los pasos:
#   SIN_TENDENCIA → ESPERANDO_RUPTURA → ESPERANDO_RETROCESO → ESPERANDO_CONFIRMACION → SENAL → EN_POSICION
# No usa MT5: el precio de entrada, el SL/TP y los filtros (horario, riesgo...) los aplica signal_market_structure.py.
# Cada cambio de estado se guarda en self.log con su motivo y la regla de la especificación (ST-5, ST-6...).

from dataclasses import dataclass
from typing import List, Optional
from .bars import Bar, BarAggregator
from .structure import StructureTracker, Swing, SwingEvent, BreakEvent, TrendEvent, BULLISH, BEARISH, RANGING

SIN_TENDENCIA = "SIN_TENDENCIA"
ESPERANDO_RUPTURA = "ESPERANDO_RUPTURA"
ESPERANDO_RETROCESO = "ESPERANDO_RETROCESO"
ESPERANDO_CONFIRMACION = "ESPERANDO_CONFIRMACION"
SENAL = "SENAL"
EN_POSICION = "EN_POSICION"

M5, M15, H1 = 300, 900, 3600


@dataclass(frozen=True)
class SetupParams:
    swing_n_h1: int = 3
    swing_n_m15: int = 3
    swing_n_m5: int = 2
    break_margin_points: float = 5
    zone_tol_points: float = 30
    retrace_max_m15: int = 16
    confirm_max_m5: int = 36
    break_kinds: str = "any"            # "any" (ST-2) o "bos" (variante V2)
    confirmation: str = "m5_sequence"   # "m5_sequence" (ST-7, ST-8) o "m15_close" (variante V3)


@dataclass(frozen=True)
class Signal:
    direction: str          # "BUY" o "SELL"
    time: int               # apertura de la vela M5 de señal
    close_time: int         # cierre de esa vela: la entrada es en la apertura de la siguiente (EN-1)
    close: float
    stop_swing: float       # L2 (HL de M5) en compras, Hb (LH de M5) en ventas: el SL va detrás (SL-1). V3: extremo desde el toque
    first: float            # L1 / Ha
    trigger: float          # Hc / Lc
    level: float            # nivel M15 roto
    origin: float           # origen de la ruptura (BOS-4)
    break_time: int
    touch_time: int


@dataclass
class _Setup:
    direction: str          # alcista o bajista
    level: float
    origin: float
    break_time: int         # cierre de la vela M15 de la ruptura
    deadline: int           # ST-5: límite para tocar la zona
    touch_index: int = -1   # vela M5 del toque
    touch_time: int = 0
    confirm_deadline: int = 0   # ST-10
    extreme: float = 0.0        # V3: mínimo (compras) o máximo (ventas) de las velas M5 desde el toque


class SetupMachine():

    def __init__(self, params: SetupParams, point: float):
        """
        Initializes the state machine of one symbol.

        Args:
            params (SetupParams): Parameters of the strategy (section 13 of the specification).
            point (float): Point of the symbol (0.00001 in EURUSD).
        """
        self.params = params
        self.point = point
        self.margin = params.break_margin_points * point
        self.tol = params.zone_tol_points * point

        self.h1 = StructureTracker(params.swing_n_h1, point, H1, params.break_margin_points)
        self.m15 = StructureTracker(params.swing_n_m15, point, M15, params.break_margin_points)
        self.m5 = StructureTracker(params.swing_n_m5, point, M5, params.break_margin_points)
        self._to_m15 = BarAggregator(M15)
        self._to_h1 = BarAggregator(H1)

        self.state = SIN_TENDENCIA
        self.direction = RANGING        # tendencia de H1
        self.setup: Optional[_Setup] = None
        self.log: List[tuple] = []      # (hora, estado anterior, estado nuevo, motivo)

    # ---------------- Cambios de estado
    def _go(self, time: int, state: str, reason: str) -> None:
        self.log.append((time, self.state, state, reason))
        self.state = state

    def _idle_state(self) -> str:
        return SIN_TENDENCIA if self.direction == RANGING else ESPERANDO_RUPTURA

    def _cancel(self, time: int, reason: str) -> None:
        self.setup = None
        self._go(time, self._idle_state(), reason)

    def discard_signal(self, time: int, reason: str) -> None:
        """The signal did not pass a filter (EN-2, EN-3, SL-2, TP-2, HO, RG): it is discarded, not kept for later."""
        if self.state == SENAL:
            self._go(time, self._idle_state(), reason)

    def sync_position(self, time: int, has_position: bool, opened_and_closed: bool = False) -> None:
        """
        Updates the state with the open positions of the symbol (§10). Called before each new bar.

        Args:
            time (int): Current server time.
            has_position (bool): True if the strategy has an open position in this symbol.
            opened_and_closed (bool): True if the order of the signal opened a position that is already closed
                (it touched the SL or the TP in the same M5 bar in which it opened).
        """
        if has_position and self.state != EN_POSICION:
            # Orden ejecutada y verificada (EN-4) o, al reiniciar, posición que ya estaba abierta
            reason = "EN-4: orden ejecutada" if self.state == SENAL else "posición abierta al arrancar"
            self.setup = None
            self._go(time, EN_POSICION, reason)
        elif not has_position and self.state == SENAL and opened_and_closed:
            self.setup = None
            self._go(time, EN_POSICION, "EN-4: orden ejecutada")
            self._go(time, self._idle_state(), "ST-11: posición cerrada en la misma vela M5 en que se abrió")
        elif not has_position and self.state == SENAL:
            self._go(time, self._idle_state(), "EN-3/EN-4: la señal no llegó a abrir posición (riesgo o broker)")
        elif not has_position and self.state == EN_POSICION:
            self._go(time, self._idle_state(), "ST-11: posición cerrada")

    # ---------------- Vela M5 nueva
    def update(self, bar: Bar) -> Optional[Signal]:
        """
        Processes one closed M5 bar: first the H1 and M15 bars it closes, then the M5 bar itself.

        Args:
            bar (Bar): The M5 bar (server time, Bid prices).

        Returns:
            Signal: The signal if this bar completes the setup (state SENAL), otherwise None.
        """
        for h1_bar in self._to_h1.update(bar):
            for event in self.h1.update(h1_bar.time, h1_bar.open, h1_bar.high, h1_bar.low, h1_bar.close):
                if isinstance(event, TrendEvent):
                    self._on_h1_trend(event)

        signal = None
        for m15_bar in self._to_m15.update(bar):
            events = self.m15.update(m15_bar.time, m15_bar.open, m15_bar.high, m15_bar.low, m15_bar.close)
            signal = self._on_m15_bar(m15_bar, events, bar) or signal

        self.m5.update(bar.time, bar.open, bar.high, bar.low, bar.close)
        return self._on_m5_bar(bar) or signal

    # ---------------- H1: dirección (ST-1)
    def _on_h1_trend(self, event: TrendEvent) -> None:
        self.direction = event.new
        if self.state in (SENAL, EN_POSICION):
            return      # con una posición abierta los cambios de H1 no la cierran (§10)
        self.setup = None
        if event.new == RANGING:
            self._go(event.time, SIN_TENDENCIA, "TR-4/CH-2: H1 lateral")
        else:
            self._go(event.time, ESPERANDO_RUPTURA, f"ST-1: H1 {event.new}")

    # ---------------- M15: ruptura y zona (ST-2, ST-3, ST-5, ST-6)
    def _on_m15_bar(self, bar: Bar, events: list, m5_bar: Bar) -> Optional[Signal]:
        close_time = bar.time + M15
        if self.setup is not None and self._closes_beyond_origin(bar.close):
            self._cancel(close_time, "ST-6: cierre M15 más allá del origen")

        if self.state == ESPERANDO_CONFIRMACION and self.params.confirmation == "m15_close":
            return self._m15_confirmation(bar, m5_bar)
        if self.state not in (ESPERANDO_RUPTURA, ESPERANDO_RETROCESO):
            return None
        for event in events:
            # V2: solo las rupturas que M15 etiqueta como BOS (M15 ya va en la dirección de H1)
            if self.params.break_kinds == "bos" and isinstance(event, BreakEvent) and event.kind != "BOS":
                continue
            if isinstance(event, BreakEvent) and event.direction == self.direction:
                replaced = self.setup is not None
                self.setup = _Setup(event.direction, event.level, event.origin, event.time,
                                    event.time + self.params.retrace_max_m15 * M15)
                rule = "ST-6 (reemplazo): nueva ruptura M15" if replaced else "ST-2: ruptura M15"
                self._go(event.time, ESPERANDO_RETROCESO, f"{rule} {event.kind} de {event.level:.5f} (origen {event.origin:.5f})")
        return None

    def _m15_confirmation(self, bar: Bar, m5_bar: Bar) -> Optional[Signal]:
        """Variant V3: signal on the first M15 close beyond the high (buys) or the low (sells) of the previous M15 bar."""
        setup = self.setup
        close_time = bar.time + M15
        if close_time > setup.confirm_deadline:
            return None     # la caducidad (ST-10) la aplica la vela M5
        bullish = setup.direction == BULLISH
        # La vela M5 actual todavía no se ha procesado: su mínimo (máximo) también cuenta para el SL
        extreme = min(setup.extreme, m5_bar.low) if bullish else max(setup.extreme, m5_bar.high)
        trigger = self.m15.highs[-2] if bullish else self.m15.lows[-2]
        if not (bar.close > trigger + self.margin if bullish else bar.close < trigger - self.margin):
            return None
        signal = Signal("BUY" if bullish else "SELL", bar.time, close_time, bar.close, extreme, extreme,
                        trigger, setup.level, setup.origin, setup.break_time, setup.touch_time)
        self.setup = None       # ST-11
        self._go(close_time, SENAL, f"ST-8: cierre M15 {bar.close:.5f} más allá de {trigger:.5f} (vela M15 anterior, V3), "
                                    f"extremo desde el toque {extreme:.5f}")
        return signal

    def _closes_beyond_origin(self, close: float) -> bool:
        if self.setup.direction == BULLISH:
            return close < self.setup.origin
        return close > self.setup.origin

    # ---------------- M5: retroceso y confirmación (ST-4, ST-7…ST-10)
    def _on_m5_bar(self, bar: Bar) -> Optional[Signal]:
        setup = self.setup
        # Solo cuentan las velas M5 posteriores a la ruptura
        if setup is None or bar.time < setup.break_time:
            return None

        close_time = bar.time + M5
        if self._closes_beyond_origin(bar.close):
            self._cancel(close_time, "ST-6: cierre M5 más allá del origen")
            return None

        bullish = setup.direction == BULLISH
        if self.state == ESPERANDO_RETROCESO:
            touched = bar.low <= setup.level + self.tol if bullish else bar.high >= setup.level - self.tol
            if touched and close_time <= setup.deadline:
                setup.touch_index = len(self.m5.times) - 1
                setup.touch_time = bar.time
                setup.confirm_deadline = close_time + self.params.confirm_max_m5 * M5
                setup.extreme = bar.low if bullish else bar.high
                self._go(close_time, ESPERANDO_CONFIRMACION, f"ST-4: toque de la zona {setup.level:.5f} ± {self.params.zone_tol_points:g} puntos")
            elif close_time >= setup.deadline:
                self._cancel(close_time, f"ST-5: no tocó la zona en {self.params.retrace_max_m15} velas M15")
            return None

        if self.state == ESPERANDO_CONFIRMACION and self.params.confirmation == "m15_close":
            setup.extreme = min(setup.extreme, bar.low) if bullish else max(setup.extreme, bar.high)
            if close_time >= setup.confirm_deadline:
                self._cancel(close_time, f"ST-10: sin confirmación en {self.params.confirm_max_m5} velas M5")
            return None

        if self.state == ESPERANDO_CONFIRMACION:
            first, trigger, second = self._confirmation_swings(bullish)
            if second is not None and close_time <= setup.confirm_deadline:
                broken = bar.close > trigger.price + self.margin if bullish else bar.close < trigger.price - self.margin
                if broken:
                    signal = Signal("BUY" if bullish else "SELL", bar.time, close_time, bar.close, second.price, first.price,
                                    trigger.price, setup.level, setup.origin, setup.break_time, setup.touch_time)
                    self.setup = None       # ST-11: cada ruptura M15 da como mucho una operación
                    self._go(close_time, SENAL, f"ST-8: cierre {bar.close:.5f} más allá de {trigger.price:.5f} "
                                                f"con {'HL' if bullish else 'LH'} {second.price:.5f} confirmado")
                    return signal
            if close_time >= setup.confirm_deadline:
                self._cancel(close_time, f"ST-10: sin confirmación en {self.params.confirm_max_m5} velas M5")
        return None

    def _confirmation_swings(self, bullish: bool) -> tuple:
        """
        ST-7 / ST-9 with the M5 swings whose pivot is after the touch. For a buy: L1 → Hc → L2 > L1.

        Returns:
            tuple: (first, trigger, second) swings; any of them can be None.
        """
        first_kind = "low" if bullish else "high"
        first = trigger = second = None
        for swing in self.m5.swings:
            if swing.index <= self.setup.touch_index:
                continue
            if swing.kind == first_kind:
                if first is None or trigger is None:
                    first = swing
                elif swing.price > first.price if bullish else swing.price < first.price:
                    second = swing
                else:
                    # ST-9: mínimo por debajo de L1 (máximo por encima de Ha en ventas) → nuevo L1 y se reinicia
                    first, trigger, second = swing, None, None
            elif first is not None:
                if second is None:
                    trigger = swing
                else:
                    # Nuevo máximo después del HL sin cierre por encima de Hc: la secuencia avanza (L2 pasa a ser L1)
                    first, trigger, second = second, swing, None
        return first, trigger, second
