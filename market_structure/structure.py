# Estructura de mercado a partir de velas OHLC: swings, etiquetas (HH/HL/LH/LL), tendencia, BOS y CHoCH.
# Sigue la especificación de la Fase 1: los identificadores (SW-1, TR-2, BOS-1...) son las reglas de ese documento.
#
# Funciona vela a vela y solo con velas YA CERRADAS: cada decisión se toma con lo conocido al cierre de la vela,
# así el resultado es el mismo en el backtest y en real, y nunca usa información futura (CV-5).

from dataclasses import dataclass
from typing import List, Optional

BULLISH = "alcista"
BEARISH = "bajista"
RANGING = "lateral"


@dataclass(frozen=True)
class Swing:
    kind: str                   # "high" o "low"
    price: float
    index: int                  # vela del pivote
    time: int                   # hora de apertura de la vela del pivote
    confirm_index: int          # vela con cuyo cierre se confirma (index + N)
    confirm_time: int           # hora de cierre de esa vela: desde aquí se puede usar (SW-2)
    label: Optional[str] = None  # HH, LH, EQH / HL, LL, EQL. None si no hay un swing anterior del mismo tipo

    @property
    def key(self) -> tuple:
        return (self.kind, self.index)


@dataclass(frozen=True)
class SwingEvent:
    swing: Swing
    replaced: Optional[Swing] = None    # swing del mismo tipo al que sustituye (SW-4)


@dataclass(frozen=True)
class BreakEvent:
    kind: str           # "BOS", "CHOCH" o "BREAK" (ruptura sin efecto en la tendencia: en rango o de un nivel no protegido)
    direction: str      # alcista o bajista
    swing: Swing        # swing roto
    index: int          # vela de la ruptura
    time: int           # hora de cierre de la vela de la ruptura
    close: float        # cierre que rompe
    origin: float       # extremo de origen (BOS-4): mínimo (ruptura alcista) o máximo (bajista) desde el pivote
    scope: str          # "externa" o "interna" (BOS-5)

    @property
    def level(self) -> float:
        return self.swing.price


@dataclass(frozen=True)
class TrendEvent:
    old: str
    new: str
    index: int
    time: int


class StructureTracker():

    def __init__(self, n: int, point: float, timeframe_seconds: int, break_margin_points: float = 5, scope: str = "externa"):
        """
        Initializes the market structure tracker of one symbol and timeframe.

        Args:
            n (int): Bars at each side of a pivot (SW-1).
            point (float): Point of the symbol (0.00001 in EURUSD).
            timeframe_seconds (int): Duration of each bar, to know when it closes.
            break_margin_points (float): The close must pass the level by this margin to be a break (BOS-1).
            scope (str): "externa" or "interna" (BOS-5). It is only copied to the break events.
        """
        if n < 1:
            raise ValueError("N debe ser al menos 1")
        self.n = n
        self.point = point
        self.timeframe_seconds = timeframe_seconds
        self.margin = break_margin_points * point
        self.scope = scope

        self.times: List[int] = []
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.closes: List[float] = []

        self.swings: List[Swing] = []       # secuencia alternada high, low, high... (SW-4)
        self.broken: set = set()            # claves de los swings ya rotos (BOS-2)
        self.trend = RANGING
        self.suspended: Optional[str] = None    # tendencia suspendida por un CHoCH (TR-4)
        self.suspended_index = -1

    # ---------------- Consultas
    def last_swing(self, kind: str) -> Optional[Swing]:
        for swing in reversed(self.swings):
            if swing.kind == kind:
                return swing
        return None

    def protected_swing(self) -> Optional[Swing]:
        """TR-3: alcista → el swing low anterior al último swing high; bajista → el swing high anterior al último swing low."""
        if self.trend == RANGING:
            return None
        last_kind = "high" if self.trend == BULLISH else "low"
        for j in range(len(self.swings) - 1, 0, -1):
            if self.swings[j].kind == last_kind:
                return self.swings[j - 1]
        return None

    def is_broken(self, swing: Swing) -> bool:
        return swing.key in self.broken

    # ---------------- Vela nueva
    def update(self, time: int, open_: float, high: float, low: float, close: float) -> list:
        """
        Processes one closed bar.

        Args:
            time (int): Opening time of the bar (server time, seconds).
            open_, high, low, close (float): Prices of the bar.

        Returns:
            list: Events of this bar, in order: new swings, trend change and breaks.
        """
        t = len(self.times)
        self.times.append(time)
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        close_time = time + self.timeframe_seconds

        # Niveles vigilados antes de esta vela: un swing nuevo puede dejar de ser el último y su ruptura no debe perderse
        watched_before = self._break_candidates()

        events = []
        # 1) Pivote que se confirma con esta vela: el de la vela t - N (SW-2)
        if t >= 2 * self.n:
            events += self._check_pivot(t - self.n, t, close_time)
        # 2) Tendencia con los swings conocidos al cierre de esta vela
        trend_event = self._update_trend(t, close_time)
        if trend_event:
            events.append(trend_event)
        # 3) Rupturas, clasificadas con la tendencia al cierre. Un pivote no puede romperse antes de confirmarse (BOS-6)
        events += self._check_breaks(t, close, close_time, watched_before)
        return events

    # ---------------- Swings
    def _check_pivot(self, i: int, t: int, close_time: int) -> list:
        n, highs, lows = self.n, self.highs, self.lows
        # SW-1: estrictamente mayor que las N anteriores y mayor o igual que las N posteriores (SW-3: con iguales gana el primero)
        is_high = all(highs[i] > highs[j] for j in range(i - n, i)) and all(highs[i] >= highs[j] for j in range(i + 1, t + 1))
        is_low = all(lows[i] < lows[j] for j in range(i - n, i)) and all(lows[i] <= lows[j] for j in range(i + 1, t + 1))

        kinds = []
        if is_high and is_low:
            # SW-5: vela envolvente. Primero el tipo opuesto al último swing para mantener la alternancia
            last = self.swings[-1].kind if self.swings else "low"
            kinds = ["low", "high"] if last == "high" else ["high", "low"]
        elif is_high:
            kinds = ["high"]
        elif is_low:
            kinds = ["low"]

        events = []
        for kind in kinds:
            event = self._add_swing(kind, highs[i] if kind == "high" else lows[i], i, t, close_time)
            if event:
                events.append(event)
        return events

    def _add_swing(self, kind: str, price: float, i: int, t: int, close_time: int) -> Optional[SwingEvent]:
        replaced = None
        if self.swings and self.swings[-1].kind == kind:
            # SW-4: dos swings seguidos del mismo tipo → se queda el más extremo
            last = self.swings[-1]
            more_extreme = price > last.price if kind == "high" else price < last.price
            if not more_extreme:
                return None
            replaced = self.swings.pop()

        swing = Swing(kind, price, i, self.times[i], t, close_time, self._label(kind, price, self.last_swing(kind)))
        self.swings.append(swing)
        return SwingEvent(swing, replaced)

    def _label(self, kind: str, price: float, previous: Optional[Swing]) -> Optional[str]:
        # TR-1. Iguales: a menos de medio punto (los precios llegan redondeados a los decimales del símbolo)
        if previous is None:
            return None
        if abs(price - previous.price) < self.point / 2:
            return "EQH" if kind == "high" else "EQL"
        if kind == "high":
            return "HH" if price > previous.price else "LH"
        return "HL" if price > previous.price else "LL"

    # ---------------- Rupturas
    def _break_candidates(self) -> list:
        # BOS-1: se vigilan el último swing high, el último swing low y el nivel protegido, si no están rotos
        candidates = []
        for swing in (self.last_swing("high"), self.last_swing("low"), self.protected_swing()):
            if swing and not self.is_broken(swing) and swing not in candidates:
                candidates.append(swing)
        return candidates

    def _check_breaks(self, t: int, close: float, close_time: int, watched_before: list) -> list:
        candidates = list(watched_before)
        for swing in self._break_candidates():
            if swing not in candidates:
                candidates.append(swing)

        events = []
        for swing in candidates:
            # Un swing sustituido por otro más extremo (SW-4) ya no forma parte de la estructura
            # (los vigilados están entre los 3 últimos, y en una vela se añaden como mucho 2 swings)
            if self.is_broken(swing) or swing not in self.swings[-5:]:
                continue
            if swing.kind == "high" and close > swing.price + self.margin:
                direction = BULLISH
                origin = min(self.lows[swing.index + 1:t + 1])
            elif swing.kind == "low" and close < swing.price - self.margin:
                direction = BEARISH
                origin = max(self.highs[swing.index + 1:t + 1])
            else:
                continue

            # BOS-3: a favor de la tendencia → BOS; el nivel protegido en contra → CHoCH; el resto → BREAK
            protected = self.protected_swing()
            if self.trend == direction:
                kind = "BOS"
            elif self.trend != RANGING and protected is not None and swing.key == protected.key:
                kind = "CHOCH"
            else:
                kind = "BREAK"

            self.broken.add(swing.key)      # BOS-2: cada nivel se rompe una sola vez
            events.append(BreakEvent(kind, direction, swing, t, close_time, close, origin, self.scope))

            if kind == "CHOCH":
                # CH-2 / TR-4: el CHoCH suspende la tendencia (no la invierte)
                events.append(TrendEvent(self.trend, RANGING, t, close_time))
                self.suspended = self.trend
                self.suspended_index = t
                self.trend = RANGING
        return events

    # ---------------- Tendencia
    def _labels_trend(self) -> str:
        # TR-2: alcista con HH + HL, bajista con LH + LL; cualquier otra combinación es lateral
        last_high, last_low = self.last_swing("high"), self.last_swing("low")
        if last_high is None or last_low is None:
            return RANGING
        if last_high.label == "HH" and last_low.label == "HL":
            return BULLISH
        if last_high.label == "LH" and last_low.label == "LL":
            return BEARISH
        return RANGING

    def _update_trend(self, t: int, close_time: int) -> Optional[TrendEvent]:
        new = self._labels_trend()

        if self.suspended is not None:
            if new == self.suspended:
                # TR-4: la tendencia suspendida solo se reanuda con un HH (o un LL) confirmado después del CHoCH
                resume_kind, resume_label = ("high", "HH") if self.suspended == BULLISH else ("low", "LL")
                resume = self.last_swing(resume_kind)
                if resume.label == resume_label and resume.confirm_index > self.suspended_index:
                    self.suspended = None
                else:
                    new = RANGING
            elif new != RANGING:
                self.suspended = None       # TR-4: las etiquetas ya marcan la tendencia contraria

        if new == self.trend:
            return None
        event = TrendEvent(self.trend, new, t, close_time)
        self.trend = new
        return event
