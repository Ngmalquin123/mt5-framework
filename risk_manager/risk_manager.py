# QUANTDEMY - https://quantdemy.com - Trading con Python y MetaTrader 5: Crea tu Propio Framework

from .interfaces.risk_manager_interface import IRiskManager
from .properties.risk_manager_properties import BaseRiskProps, MaxLeverageFactorRiskProps, TradingLimitsProps
from .risk_managers.max_leverage_factor_risk_manager import MaxLeverageFactorRiskManager
from data_provider.data_provider import DataProvider
from portfolio.portfolio import Portfolio
from events.events import SizingEvent, OrderEvent
from utils.utils import Utils
from queue import Queue
from datetime import datetime, timezone
import MetaTrader5 as mt5

class RiskManager(IRiskManager):
    
    def __init__(self, events_queue: Queue, data_provider: DataProvider, portfolio: Portfolio, risk_properties: BaseRiskProps,
                trading_limits: TradingLimitsProps = None):
        """
        Initializes a RiskManager object.

        Args:
            events_queue (Queue): The queue for receiving events.
            data_provider (DataProvider): The data provider for retrieving market data.
            portfolio (Portfolio): The portfolio object for managing positions and balances.
            risk_properties (BaseRiskProps): The risk properties object for configuring risk management.
            trading_limits (TradingLimitsProps, optional): General limits (daily loss, positions, spread, hours).

        Returns:
            None
        """
        self.events_queue = events_queue
        self.DATA_PROVIDER = data_provider
        self.PORTFOLIO = portfolio
        self.limits = trading_limits
        
        self.risk_management_method = self._get_risk_management_method(risk_properties)
    
    def _get_risk_management_method(self, risk_props: BaseRiskProps) -> IRiskManager:
        """
        Returns the appropriate risk management method based on the given risk properties.

        Args:
            risk_props (BaseRiskProps): The risk properties object.

        Returns:
            IRiskManager: An instance of the appropriate risk manager.

        Raises:
            Exception: If the risk management method is unknown.
        """
        if isinstance(risk_props, MaxLeverageFactorRiskProps):
            return MaxLeverageFactorRiskManager(risk_props)
        else:
            raise Exception(f"ERROR: Método de Risk Mgmt desconocido: {risk_props}")

    def _compute_current_value_of_positions_in_account_currency(self) -> float:
        """
        Computes the current value of positions in the account currency.

        Returns:
            float: The total value of the positions in the account currency.
        """

        # Recopilamos las posiciones abiertas por nuestra estrategia
        current_positions = self.PORTFOLIO.get_strategy_open_positions()

        # Vamos a calcular el valor de las posiciones abiertas
        total_value = 0.0
        for position in current_positions:
            total_value += self._compute_value_of_position_in_account_currency(position.symbol, position.volume, position.type)
        
        return total_value

    def _compute_value_of_position_in_account_currency(self, symbol: str, volume: float, position_type: int) -> float:
        """
        Computes the value of a position in the account currency.

        Args:
            symbol (str): The symbol of the position.
            volume (float): The volume of the position.
            position_type (int): The type of the position.

        Returns:
            float: The value of the position in the account currency.
        """
        symbol_info = mt5.symbol_info(symbol)

        # Unidades operadas en las unidades del symbol: (cantidad de moneda base, barriles de petroleo, onzas de oro)
        traded_units = volume * symbol_info.trade_contract_size

        # Valor de las unidades operadas en la divisa cotizada del símbolo (USD para el oro, USD para el petróleo, CHF para el GBPCHF, EUR para el DAX)
        value_traded_in_profit_ccy = traded_units * self.DATA_PROVIDER.get_latest_tick(symbol)['bid']

        # Valor de las unidades operadas en la divisa de nuestra cuenta de trading (la cuenta en el broker, la cuenta MT5)
        value_traded_in_account_ccy = Utils.convert_currency_amount_to_another_currency(value_traded_in_profit_ccy, symbol_info.currency_profit, mt5.account_info().currency)

        if position_type == mt5.ORDER_TYPE_SELL:
            return -value_traded_in_account_ccy
        else:
            return value_traded_in_account_ccy

    def _create_and_put_order_event(self, sizing_event: SizingEvent, volume: float) -> None:
        """
        Creates an OrderEvent based on the given SizingEvent and volume, and puts it into the events queue.

        Args:
            sizing_event (SizingEvent): The sizing event to create the order event from.
            volume (float): The volume for the order event.

        Returns:
            None
        """
        # Creamos el Order event a partir del sizing event y el volume
        order_event = OrderEvent(symbol=sizing_event.symbol,
                                    signal=sizing_event.signal,
                                    target_order=sizing_event.target_order,
                                    target_price=sizing_event.target_price,
                                    magic_number=sizing_event.magic_number,
                                    sl=sizing_event.sl,
                                    tp=sizing_event.tp,
                                    volume=volume)

        # Colocamos el order event a la cola de eventos
        self.events_queue.put(order_event)
    
    def _compute_daily_loss_pct(self, server_timestamp: int) -> float:
        """
        Computes the loss of the strategy today (closed deals + open positions) as a fraction
        of the balance at the start of the day. A positive value means a loss.

        Args:
            server_timestamp (int): Current server time (seconds), taken from the last tick.

        Returns:
            float: The daily loss as a fraction (0.02 = 2%).
        """
        # Inicio del día en hora del servidor (el time de MT5 está en hora del servidor)
        day_start = server_timestamp - server_timestamp % 86400

        # Resultado de las operaciones cerradas hoy por la estrategia
        deals = mt5.history_deals_get(day_start, server_timestamp + 60) or ()
        realized = sum(deal.profit + deal.commission + deal.swap for deal in deals if deal.magic == self.PORTFOLIO.magic)

        # Resultado flotante de las posiciones abiertas por la estrategia
        floating = sum(position.profit + position.swap for position in self.PORTFOLIO.get_strategy_open_positions())

        balance_at_day_start = mt5.account_info().balance - realized
        if balance_at_day_start <= 0:
            return 0.0

        return -(realized + floating) / balance_at_day_start

    def _is_in_no_trading_window(self, server_time: datetime) -> bool:
        """
        Checks if the server time is inside the period without new trades.

        Args:
            server_time (datetime): Current server time.

        Returns:
            bool: True if no new trades should be opened.
        """
        start = datetime.strptime(self.limits.no_trading_start, "%H:%M").time()
        end = datetime.strptime(self.limits.no_trading_end, "%H:%M").time()
        now = server_time.time()

        if start <= end:
            return start <= now < end

        # La ventana cruza la medianoche (p.ej. de 23:45 a 01:15)
        return now >= start or now < end

    def _check_trading_limits(self, sizing_event: SizingEvent) -> bool:
        """
        Checks the general trading limits before assessing the order.

        Args:
            sizing_event (SizingEvent): The sizing event to check.

        Returns:
            bool: True if the order can go on, False if a limit blocks it.
        """
        if self.limits is None:
            return True

        symbol = sizing_event.symbol
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"{Utils.dateprint()} - RISK MGMT: No se ha podido recuperar el último tick de {symbol}. Orden descartada")
            return False

        # 1) Pérdida diaria máxima
        if self.limits.max_daily_loss_pct > 0:
            daily_loss_pct = self._compute_daily_loss_pct(tick.time)
            if daily_loss_pct >= self.limits.max_daily_loss_pct:
                print(f"{Utils.dateprint()} - RISK MGMT: La pérdida del día ({daily_loss_pct:.2%}) alcanza el máximo de {self.limits.max_daily_loss_pct:.2%}. No se abren más operaciones hoy")
                return False

        # 2) Número máximo de posiciones abiertas
        if self.limits.max_open_positions > 0:
            open_positions = len(self.PORTFOLIO.get_strategy_open_positions())
            if open_positions >= self.limits.max_open_positions:
                print(f"{Utils.dateprint()} - RISK MGMT: Ya hay {open_positions} posiciones abiertas (máx. {self.limits.max_open_positions}). Orden descartada en {symbol}")
                return False

        # 3) Spread máximo
        if self.limits.max_spread_points > 0:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is not None and symbol_info.spread > self.limits.max_spread_points:
                print(f"{Utils.dateprint()} - RISK MGMT: Spread de {symbol} de {symbol_info.spread} puntos (máx. {self.limits.max_spread_points}). Orden descartada")
                return False

        # 4) Horario sin nuevas operaciones (hora del servidor)
        if self.limits.no_trading_start and self.limits.no_trading_end:
            server_time = datetime.fromtimestamp(tick.time, tz=timezone.utc).replace(tzinfo=None)
            if self._is_in_no_trading_window(server_time):
                print(f"{Utils.dateprint()} - RISK MGMT: {server_time:%H:%M} (hora servidor) está dentro del horario sin operar ({self.limits.no_trading_start}-{self.limits.no_trading_end}). Orden descartada en {symbol}")
                return False

        return True

    def assess_order(self, sizing_event: SizingEvent) -> None:
        """
        Assess the order based on the risk management method and create an order event if the new volume is greater than 0.

        Args:
            sizing_event (SizingEvent): The sizing event containing information about the order.

        Returns:
            None
        """
        # Límites generales: pérdida diaria, nº de posiciones, spread y horario
        if not self._check_trading_limits(sizing_event):
            return

        # Obtenemos el valor de todas las posiciones abiertas por la estrategia en la divisa de la cuenta
        current_position_value = self._compute_current_value_of_positions_in_account_currency()

        # Obtenemos el valor que tendría la nueva posición, también en la divisa de la cuenta
        position_type = mt5.ORDER_TYPE_BUY if sizing_event.signal == "BUY" else mt5.ORDER_TYPE_SELL
        new_position_value = self._compute_value_of_position_in_account_currency(sizing_event.symbol, sizing_event.volume, position_type)
        
        # Obtenemos el nuevo volumen de la operacion que queremos ejecutar después de pasar por el risk manager
        new_volume = self.risk_management_method.assess_order(sizing_event, current_position_value, new_position_value)

        # Evaluamos el nuevo volumen
        if new_volume > 0.0:
            # colocar el order event a la cola de eventos
            self._create_and_put_order_event(sizing_event, new_volume)