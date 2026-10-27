# Backtest de la estrategia con los MISMOS módulos y la MISMA configuración (config.toml) que trading_app.py.
#
# 1) Descarga los datos (en Windows con MT5 abierto), con el timeframe de config.toml:
#    uv run python -m backtest.download_data --symbols EURUSD USDJPY GBPUSD --timeframe 1min --start 2026-08-01
# 2) Ejecuta el backtest (funciona en cualquier sistema, no necesita MT5):
#    uv run backtest_app.py                     (lee config.toml)
#    uv run backtest_app.py otra_config.toml    (para probar otros parámetros sin tocar los del real)

from backtest.simulated_mt5 import SimulatedMT5
from utils.config import FrameworkConfig
from datetime import datetime
import sys

if __name__ == "__main__":

    # Misma configuración que el real. Lo propio del backtest (fechas, balance, comisión) está en [backtest]
    CONFIG = FrameworkConfig(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    BT = CONFIG.backtest
    print(CONFIG.summary())

    # Broker simulado: periodo del backtest, cuenta y costes
    SIM = SimulatedMT5(symbols=CONFIG.symbols,
                        timeframe=CONFIG.timeframe,
                        start=BT.get("start"),
                        end=BT.get("end"),
                        data_dir=BT.get("data_dir", "backtest/data"),
                        initial_balance=BT.get("initial_balance", 10000.0),
                        account_currency=BT.get("account_currency", "USD"),
                        commission_per_lot=BT.get("commission_per_lot", 0.0),
                        spread_points=BT.get("spread_points") or None)

    # IMPORTANTE: activar el MT5 simulado ANTES de importar los módulos del Framework
    SIM.activate()

    from data_provider.data_provider import DataProvider
    from trading_director.trading_director import TradingDirector
    from signal_generator.signal_generator import SignalGenerator
    from position_sizer.position_sizer import PositionSizer
    from portfolio.portfolio import Portfolio
    from risk_manager.risk_manager import RiskManager
    from order_executor.order_executor import OrderExecutor
    from notifications.notifications import NotificationService, TelegramNotificationProperties
    from trade_journal.trade_journal import TradeJournal
    from backtest.backtester import Backtester
    from queue import Queue

    # Creación de la cola de eventos principal
    events_queue = Queue()

    # Creación de los módulos principales del Framework (igual que en trading_app.py)
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

    # En el backtest no se mandan mensajes de Telegram
    NOTIFICATIONS = NotificationService(properties=TelegramNotificationProperties(token="", chat_id=""))

    # Cada backtest guarda sus operaciones en un CSV nuevo
    TRADE_JOURNAL = TradeJournal(magic_number=CONFIG.magic_number,
                                strategy_name=CONFIG.strategy_name,
                                file_path=f"backtest/results/backtest_{datetime.now():%Y%m%d_%H%M%S}_trades.csv")

    TRADING_DIRECTOR = TradingDirector(events_queue=events_queue,
                                        data_provider=DATA_PROVIDER,
                                        signal_generator=SIGNAL_GENERATOR,
                                        position_sizer=POSITION_SIZER,
                                        risk_manager=RISK_MANAGER,
                                        order_executor=ORDER_EXECUTOR,
                                        notification_service=NOTIFICATIONS)

    Backtester(SIM, TRADING_DIRECTOR, TRADE_JOURNAL).run()
