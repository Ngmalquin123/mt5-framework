# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework
#
# Uso:  uv run trading_app.py                 (lee config.toml)
#       uv run trading_app.py otra_config.toml

from platform_connector.platform_connector import PlatformConnector
from data_provider.data_provider import DataProvider
from trading_director.trading_director import TradingDirector
from signal_generator.signal_generator import SignalGenerator
from position_sizer.position_sizer import PositionSizer
from portfolio.portfolio import Portfolio
from risk_manager.risk_manager import RiskManager
from order_executor.order_executor import OrderExecutor
from notifications.notifications import NotificationService, TelegramNotificationProperties
from trade_journal.trade_journal import TradeJournal
from queue import Queue
from utils.config import FrameworkConfig
from utils.logger import TeeLogger
from dotenv import load_dotenv, find_dotenv
import os
import sys

if __name__ == "__main__":

    # Todo lo que se imprime se guarda también en logs/trading_YYYY-MM-DD.log
    TeeLogger.start()

    # Cargamos las variables del .env (credenciales de MT5 y Telegram)
    load_dotenv(find_dotenv())

    # Parámetros de la estrategia, del riesgo y de los límites: todo en config.toml (el mismo que usa el backtest)
    CONFIG = FrameworkConfig(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    print(CONFIG.summary())

    # Creación de la cola de eventos principal
    events_queue = Queue()

    # Creación de los módulos principales del Framework
    CONNECT = PlatformConnector(symbol_list=CONFIG.symbols)
    DATA_PROVIDER = DataProvider(events_queue=events_queue,
                                symbol_list=CONFIG.symbols,
                                timeframe=CONFIG.timeframe)

    PORTFOLIO = Portfolio(magic_number=CONFIG.magic_number)

    ORDER_EXECUTOR = OrderExecutor(events_queue=events_queue,
                                    portfolio=PORTFOLIO,
                                    pending_order_max_age_minutes=CONFIG.pending_order_max_age_minutes)

    SIGNAL_GENERATOR = SignalGenerator(events_queue=events_queue,
                                        data_provider=DATA_PROVIDER,
                                        portfolio=PORTFOLIO,
                                        order_executor=ORDER_EXECUTOR,
                                        signal_properties=CONFIG.signal_props)

    POSITION_SIZER = PositionSizer(events_queue=events_queue,
                                    data_provider=DATA_PROVIDER,
                                    sizing_properties=CONFIG.sizing_props)

    RISK_MANAGER = RiskManager(events_queue=events_queue,
                                data_provider=DATA_PROVIDER,
                                portfolio=PORTFOLIO,
                                risk_properties=CONFIG.risk_props,
                                trading_limits=CONFIG.trading_limits)

    NOTIFICATIONS = NotificationService(
        properties=TelegramNotificationProperties(
            token=os.getenv("TELEGRAM_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
    )

    # Diario de operaciones: cada operación cerrada se guarda en un CSV y se avisa por Telegram
    TRADE_JOURNAL = TradeJournal(magic_number=CONFIG.magic_number,
                                strategy_name=CONFIG.strategy_name,
                                file_path=CONFIG.journal_file)

    # Creación del trading director y ejecución del método principal
    TRADING_DIRECTOR = TradingDirector(events_queue=events_queue,
                                        data_provider=DATA_PROVIDER,
                                        signal_generator=SIGNAL_GENERATOR,
                                        position_sizer=POSITION_SIZER,
                                        risk_manager=RISK_MANAGER,
                                        order_executor=ORDER_EXECUTOR,
                                        notification_service=NOTIFICATIONS,
                                        platform_connector=CONNECT,
                                        trade_journal=TRADE_JOURNAL)

    TRADING_DIRECTOR.execute()
