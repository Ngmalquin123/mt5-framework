# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from data_provider.data_provider import DataProvider
from signal_generator.interfaces.signal_generator_interface import ISignalGenerator
from position_sizer.position_sizer import PositionSizer
from risk_manager.risk_manager import RiskManager
from order_executor.order_executor import OrderExecutor
from notifications.notifications import NotificationService
from events.events import DataEvent, SignalEvent, SizingEvent, OrderEvent, ExecutionEvent, PlacedPendingOrderEvent
from utils.utils import Utils
from platform_connector.platform_connector import PlatformConnector
from typing import Dict, Callable
import MetaTrader5 as mt5
import traceback
import queue
import time


class TradingDirector():
    
    def __init__(self, events_queue: queue.Queue, data_provider: DataProvider, signal_generator: ISignalGenerator,
                position_sizer: PositionSizer, risk_manager: RiskManager, order_executor: OrderExecutor, notification_service: NotificationService,
                platform_connector: PlatformConnector = None):
        """
        Initializes the TradingDirector object.

        Args:
            events_queue (queue.Queue): The queue to receive events.
            data_provider (DataProvider): The data provider object.
            signal_generator (ISignalGenerator): The signal generator object.
            position_sizer (PositionSizer): The position sizer object.
            risk_manager (RiskManager): The risk manager object.
            order_executor (OrderExecutor): The order executor object.
            notification_service (NotificationService): The notification service object.
            platform_connector (PlatformConnector, optional): Used to check and restore the connection with MT5.
        """
        self.events_queue = events_queue
        
        # Referencia de los distintos módulos
        self.DATA_PROVIDER = data_provider
        self.SIGNAL_GENERATOR = signal_generator
        self.POSITION_SIZER = position_sizer
        self.RISK_MANAGER = risk_manager
        self.ORDER_EXECUTOR = order_executor
        self.NOTIFICATIONS = notification_service
        self.PLATFORM_CONNECTOR = platform_connector

        # Controlador de trading
        self.continue_trading: bool = True

        # Control de errores: si hay demasiados errores seguidos detenemos el Framework
        self.consecutive_errors: int = 0
        self.max_consecutive_errors: int = 20

        # Control de la conexión con MT5 (se comprueba cada connection_check_seconds)
        self.connection_check_seconds: float = 5.0
        self._last_connection_check: float = 0.0
        self._connected: bool = True

        # Creación del event handler
        self.event_handler: Dict[str, Callable] = {
            "DATA": self._handle_data_event,
            "SIGNAL": self._handle_signal_event,
            "SIZING": self._handle_sizing_event,
            "ORDER": self._handle_order_event,
            "EXECUTION": self._handle_execution_event,
            "PENDING": self._handle_pending_order_event
        }

    def _handle_data_event(self, event: DataEvent):
        """
        Handle the data event.

        Args:
            event (DataEvent): The data event object.

        Returns:
            None
        """
        # Aquí dentro gestionamos los eventos de tipo DataEvent
        print(f"{Utils.dateprint()} - Recibido DATA EVENT de {event.symbol} - Último precio de cierre: {event.data.close}")
        self.SIGNAL_GENERATOR.generate_signal(event)

    def _handle_signal_event(self, event: SignalEvent):
        """
        Handle the signal event.

        Args:
            event (SignalEvent): The signal event object.

        Returns:
            None
        """
        # Procesamos el signal event
        print(f"{Utils.dateprint()} - Recibido SIGNAL EVENT {event.signal} para {event.symbol}")
        self.POSITION_SIZER.size_signal(event)

    def _handle_sizing_event(self, event: SizingEvent):
        """
        Handle the sizing event.

        Args:
            event (SizingEvent): The sizing event object.

        Returns:
            None
        """
        print(f"{Utils.dateprint()} - Recibido SIZING EVENT con volumen {event.volume} para {event.signal} en {event.symbol}")
        self.RISK_MANAGER.assess_order(event)

    def _handle_order_event(self, event: OrderEvent):
        """
        Handle the order event.

        Args:
            event (OrderEvent): The order event object.

        Returns:
            None
        """
        print(f"{Utils.dateprint()} - Recibido ORDER EVENT con volumen {event.volume} para {event.signal} en {event.symbol}")
        self.ORDER_EXECUTOR.execute_order(event)

    def _handle_execution_event(self, event: ExecutionEvent):
        """
        Handle the execution event.

        Args:
            event (ExecutionEvent): The execution event object.

        Returns:
            None
        """
        print(f"{Utils.dateprint()} - Recibido EXECUTION EVENT {event.signal} en {event.symbol} con volumen {event.volume} al precio {event.fill_price}")
        self._process_execution_or_pending_events(event)

    def _handle_pending_order_event(self, event: PlacedPendingOrderEvent):
        """
        Handle the pending order event.

        Args:
            event (PlacedPendingOrderEvent): The pending order event object.

        Returns:
            None
        """
        print(f"{Utils.dateprint()} - Recibido PLACED PENDING ORDER EVENT con volumen {event.volume} para {event.signal} {event.target_order} en {event.symbol} al precio {event.target_price}")
        self._process_execution_or_pending_events(event)

    def _process_execution_or_pending_events(self, event: ExecutionEvent | PlacedPendingOrderEvent):    # Utilizar el | es para python 3.10 o superior
        """
        Process the execution or pending events.

        Args:
            event (ExecutionEvent | PlacedPendingOrderEvent): The event to be processed.

        Returns:
            None
        """
        if isinstance(event, ExecutionEvent):
            self.NOTIFICATIONS.send_notification(title=f"{event.symbol} - MARKET ORDER", message=f"{Utils.dateprint()} - Ejecutada MARKET ORDER {event.signal} en {event.symbol} con volumen {event.volume} al precio {event.fill_price}")
        elif isinstance(event, PlacedPendingOrderEvent):
            self.NOTIFICATIONS.send_notification(title=f"{event.symbol} - PENDING PLACED", message=f"{Utils.dateprint()} - Colocada PENDING ORDER con volumen {event.volume} para {event.signal} {event.target_order} en {event.symbol} al precio {event.target_price}")
        else:
            pass

    def _handle_none_event(self, event):
        """
        Handles the case when a None event is received.
        
        Prints an error message and sets `continue_trading` flag to False, terminating the execution of the Framework.
        
        Args:
            event: The None event received.
        """
        print(f"{Utils.dateprint()} - ERROR: Recibido evento nulo. Terminando ejecución del Framework")
        self.continue_trading = False

    def _handle_unknown_event(self, event):
        """
        Handles the case when an Unknown event is received.
        
        Prints an error message and sets `continue_trading` flag to False, terminating the execution of the Framework.
        
        Args:
            event: The Unknown event received.
        """
        print(f"{Utils.dateprint()} - ERROR: Recibido evento desconocido. Terminando ejecución del Framework. Evento: {event}")
        self.continue_trading = False

    def _check_platform_connection(self) -> bool:
        """
        Checks every few seconds that MT5 is connected and tries to reconnect if it is not.

        Returns:
            bool: True if the platform is connected, False otherwise.
        """
        if self.PLATFORM_CONNECTOR is None or time.time() - self._last_connection_check < self.connection_check_seconds:
            return self._connected

        self._last_connection_check = time.time()
        connected = self.PLATFORM_CONNECTOR.ensure_connection()

        # Avisamos solo cuando cambia el estado de la conexión
        if connected and not self._connected:
            self.NOTIFICATIONS.send_notification(title="CONEXIÓN RESTABLECIDA", message=f"{Utils.dateprint()} - La conexión con MT5 se ha restablecido")
        elif not connected and self._connected:
            self.NOTIFICATIONS.send_notification(title="CONEXIÓN PERDIDA", message=f"{Utils.dateprint()} - Se ha perdido la conexión con MT5. Reintentando...")

        self._connected = connected
        return connected

    def _handle_loop_exception(self, exception: Exception) -> None:
        """
        Handles an unexpected error in the main loop without stopping the Framework,
        unless there are too many consecutive errors.

        Args:
            exception (Exception): The exception raised inside the main loop.
        """
        self.consecutive_errors += 1
        print(f"{Utils.dateprint()} - ERROR inesperado en el bucle principal ({self.consecutive_errors}/{self.max_consecutive_errors}): {exception}")
        # chain=False: sin el "During handling of queue.Empty..." que no aporta nada
        traceback.print_exception(type(exception), exception, exception.__traceback__, chain=False)

        # Notificamos solo el primer error de una racha para no saturar Telegram
        if self.consecutive_errors == 1:
            self.NOTIFICATIONS.send_notification(title="ERROR EN EL FRAMEWORK", message=f"{Utils.dateprint()} - {type(exception).__name__}: {exception}")

        if self.consecutive_errors >= self.max_consecutive_errors:
            print(f"{Utils.dateprint()} - ERROR: Demasiados errores seguidos. Deteniendo el Framework")
            self.NOTIFICATIONS.send_notification(title="FRAMEWORK DETENIDO", message=f"{Utils.dateprint()} - Detenido tras {self.consecutive_errors} errores seguidos. Último error: {exception}")
            self.continue_trading = False

        # Pausa para no repetir el mismo error cientos de veces por segundo
        time.sleep(1)

    def execute(self) -> None:
        """
        Executes the main trading loop.

        This method continuously checks for events in the events queue and handles them accordingly.
        If no events are available, it checks for new data from the data provider.
        The loop continues until the `continue_trading` flag is set to False.

        Note:
        - The events are processed by the corresponding event handlers.
        - If an unknown event is encountered, it is handled by the `_handle_unknown_event` method.
        - If a None event is encountered, it is handled by the `_handle_none_event` method.

        Returns:
        None
        """
        # Definición del bucle principal
        try:
            while self.continue_trading:
                try:
                    # Si no hay conexión con MT5 esperamos antes de volver a intentarlo
                    if not self._check_platform_connection():
                        time.sleep(1)
                        continue

                    try:
                        event = self.events_queue.get(block=False)    # Recordar que es una cola FIFO

                    except queue.Empty:
                        self.DATA_PROVIDER.check_for_new_data()

                    else:
                        if event is not None:
                            handler = self.event_handler.get(event.event_type, self._handle_unknown_event)
                            handler(event)
                        else:
                            self._handle_none_event(event)

                except Exception as e:
                    # Un error inesperado no detiene el Framework
                    self._handle_loop_exception(e)

                else:
                    self.consecutive_errors = 0

                time.sleep(0.01)

        except KeyboardInterrupt:
            # Cierre ordenado con Ctrl+C
            print(f"{Utils.dateprint()} - Framework detenido manualmente (Ctrl+C)")
            self.NOTIFICATIONS.send_notification(title="FRAMEWORK DETENIDO", message=f"{Utils.dateprint()} - Framework detenido manualmente")

        finally:
            mt5.shutdown()

        print(f"{Utils.dateprint()} - FIN")
