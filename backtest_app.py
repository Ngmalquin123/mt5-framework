# Backtest de la estrategia con los MISMOS módulos del Framework que trading_app.py, sobre velas históricas.
#
# 1) Descarga los datos (en Windows con MT5 abierto):
#    uv run python -m backtest.download_data --symbols EURUSD USDJPY GBPUSD --timeframe 1h --start 2025-01-01
# 2) Ejecuta el backtest (funciona en cualquier sistema, no necesita MT5):
#    uv run backtest_app.py

from backtest.simulated_mt5 import SimulatedMT5
from datetime import datetime

if __name__ == "__main__":

    # Definición de variables necesarias para la estrategia
    symbols = ['EURUSD', 'USDJPY', 'GBPUSD']
    timeframe = '1h'
    magic_number = 12345

    # Broker simulado: periodo del backtest, cuenta y costes
    SIM = SimulatedMT5(symbols=symbols,
                        timeframe=timeframe,
                        start="2025-01-01",
                        end="2026-10-01",
                        data_dir="backtest/data",
                        initial_balance=10000.0,
                        account_currency="USD",
                        commission_per_lot=0.0)     # comisión por lote y por lado (entrada y salida)

    # IMPORTANTE: activar el MT5 simulado ANTES de importar los módulos del Framework
    SIM.activate()

    from data_provider.data_provider import DataProvider
    from trading_director.trading_director import TradingDirector
    from signal_generator.signal_generator import SignalGenerator
    from signal_generator.properties.signal_generator_properties import MACrossoverProps, RSIProps
    from position_sizer.position_sizer import PositionSizer
    from position_sizer.properties.position_sizer_properties import MinSizingProps, FixedSizingProps, RiskPctSizingProps
    from portfolio.portfolio import Portfolio
    from risk_manager.risk_manager import RiskManager
    from risk_manager.properties.risk_manager_properties import MaxLeverageFactorRiskProps, TradingLimitsProps
    from order_executor.order_executor import OrderExecutor
    from notifications.notifications import NotificationService, TelegramNotificationProperties
    from trade_journal.trade_journal import TradeJournal
    from backtest.backtester import Backtester
    from queue import Queue

    mac_props = MACrossoverProps(timeframe=timeframe,
                                fast_period=5,
                                slow_period=10)

    rsi_props = RSIProps(timeframe=timeframe,
                        rsi_period=5,
                        rsi_upper=70.0,
                        rsi_lower=30.0,
                        sl_points=50,
                        tp_points=100)

    # Creación de la cola de eventos principal
    events_queue = Queue()

    # Creación de los módulos principales del Framework (igual que en trading_app.py)
    DATA_PROVIDER = DataProvider(events_queue=events_queue,
                                symbol_list=symbols,
                                timeframe=timeframe)

    PORTFOLIO = Portfolio(magic_number=magic_number)

    ORDER_EXECUTOR = OrderExecutor(events_queue=events_queue,
                                    portfolio=PORTFOLIO,
                                    pending_order_max_age_minutes=60)

    SIGNAL_GENERATOR = SignalGenerator(events_queue=events_queue,
                                        data_provider=DATA_PROVIDER,
                                        portfolio=PORTFOLIO,
                                        order_executor=ORDER_EXECUTOR,
                                        signal_properties=rsi_props)

    POSITION_SIZER = PositionSizer(events_queue=events_queue,
                                    data_provider=DATA_PROVIDER,
                                    sizing_properties=FixedSizingProps(volume=0.05))

    RISK_MANAGER = RiskManager(events_queue=events_queue,
                                data_provider=DATA_PROVIDER,
                                portfolio=PORTFOLIO,
                                risk_properties=MaxLeverageFactorRiskProps(max_leverage_factor=5),
                                trading_limits=TradingLimitsProps(max_daily_loss_pct=0.02,
                                                                  max_open_positions=3,
                                                                  max_spread_points=20,
                                                                  no_trading_start="23:45",
                                                                  no_trading_end="01:15"))

    # En el backtest no se mandan mensajes de Telegram
    NOTIFICATIONS = NotificationService(properties=TelegramNotificationProperties(token="", chat_id=""))

    # Cada backtest guarda sus operaciones en un CSV nuevo
    TRADE_JOURNAL = TradeJournal(magic_number=magic_number,
                                strategy_name="RSI",
                                file_path=f"backtest/results/backtest_{datetime.now():%Y%m%d_%H%M%S}_trades.csv")

    TRADING_DIRECTOR = TradingDirector(events_queue=events_queue,
                                        data_provider=DATA_PROVIDER,
                                        signal_generator=SIGNAL_GENERATOR,
                                        position_sizer=POSITION_SIZER,
                                        risk_manager=RISK_MANAGER,
                                        order_executor=ORDER_EXECUTOR,
                                        notification_service=NOTIFICATIONS)

    Backtester(SIM, TRADING_DIRECTOR, TRADE_JOURNAL).run()
