# Crea todos los módulos del Framework sobre un MT5 simulado y ejecuta un backtest con la configuración de config.toml

from backtest.simulated_mt5 import SimulatedMT5
from utils.config import FrameworkConfig
from datetime import datetime
from queue import Queue


def run_backtest(config: FrameworkConfig, timeframe: str = None, start: str = None, end: str = None,
                results_name: str = None) -> dict:
    """
    Runs a backtest with the modules and parameters of the configuration.

    Args:
        config (FrameworkConfig): The configuration (the same one the real trading uses).
        timeframe (str, optional): Timeframe to test instead of the one of the configuration.
        start (str, optional): First date instead of the one of [backtest].
        end (str, optional): Last date instead of the one of [backtest].
        results_name (str, optional): Name of the result files. By default backtest_<date>.

    Returns:
        dict: The statistics of the backtest.
    """
    backtest = config.backtest
    timeframe = timeframe or config.timeframe

    # Broker simulado: periodo del backtest, cuenta y costes
    sim = SimulatedMT5(symbols=config.symbols,
                       timeframe=timeframe,
                       start=start or backtest.get("start"),
                       end=end or backtest.get("end"),
                       data_dir=backtest.get("data_dir", "backtest/data"),
                       initial_balance=backtest.get("initial_balance", 10000.0),
                       account_currency=backtest.get("account_currency", "USD"),
                       commission_per_lot=backtest.get("commission_per_lot", 0.0),
                       spread_points=backtest.get("spread_points") or None)

    # IMPORTANTE: activar el MT5 simulado ANTES de importar los módulos del Framework
    sim.activate()
    try:
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

        # La estrategia con el timeframe del backtest (puede ser distinto al de la configuración)
        signal_props = config.signal_props.model_copy(update={"timeframe": timeframe})

        # Creación de la cola de eventos principal
        events_queue = Queue()

        # Creación de los módulos principales del Framework (igual que en trading_app.py)
        DATA_PROVIDER = DataProvider(events_queue=events_queue,
                                     symbol_list=config.symbols,
                                     timeframe=timeframe)

        PORTFOLIO = Portfolio(magic_number=config.magic_number)

        ORDER_EXECUTOR = OrderExecutor(events_queue=events_queue,
                                       portfolio=PORTFOLIO,
                                       pending_order_max_age_minutes=config.pending_order_max_age_minutes)

        SIGNAL_GENERATOR = SignalGenerator(events_queue=events_queue,
                                           data_provider=DATA_PROVIDER,
                                           portfolio=PORTFOLIO,
                                           order_executor=ORDER_EXECUTOR,
                                           signal_properties=signal_props)

        POSITION_SIZER = PositionSizer(events_queue=events_queue,
                                       data_provider=DATA_PROVIDER,
                                       sizing_properties=config.sizing_props)

        RISK_MANAGER = RiskManager(events_queue=events_queue,
                                   data_provider=DATA_PROVIDER,
                                   portfolio=PORTFOLIO,
                                   risk_properties=config.risk_props,
                                   trading_limits=config.trading_limits)

        # En el backtest no se mandan mensajes de Telegram
        NOTIFICATIONS = NotificationService(properties=TelegramNotificationProperties(token="", chat_id=""))

        # Cada backtest guarda sus operaciones en un CSV nuevo
        name = results_name or f"backtest_{datetime.now():%Y%m%d_%H%M%S}"
        TRADE_JOURNAL = TradeJournal(magic_number=config.magic_number,
                                     strategy_name=config.strategy_name,
                                     file_path=f"backtest/results/{name}_trades.csv")

        TRADING_DIRECTOR = TradingDirector(events_queue=events_queue,
                                           data_provider=DATA_PROVIDER,
                                           signal_generator=SIGNAL_GENERATOR,
                                           position_sizer=POSITION_SIZER,
                                           risk_manager=RISK_MANAGER,
                                           order_executor=ORDER_EXECUTOR,
                                           notification_service=NOTIFICATIONS)

        stats = Backtester(sim, TRADING_DIRECTOR, TRADE_JOURNAL).run()

        # Estrategias que registran sus decisiones (market_structure): resumen y CSV con el motivo de cada paso
        strategy = SIGNAL_GENERATOR.signal_generator_method
        if hasattr(strategy, "save_decision_log"):
            stats["decisions_file"] = strategy.save_decision_log(f"backtest/results/{name}_decisions.csv")
            stats["decisions"] = strategy.decision_log()
            for line in strategy.report_lines():
                print(line)
            print(f" Decisiones: {stats['decisions_file']}")
            print("")
        stats["trades_file"] = TRADE_JOURNAL.file_path
        return stats

    finally:
        sim.deactivate()
