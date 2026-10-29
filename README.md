# MT5 Framework

Framework de trading algorítmico por eventos para MetaTrader 5 en Python, basado en el curso
*Trading con Python y MetaTrader 5* de Quantdemy.

```
Data Provider → Signal Generator → Position Sizer → Risk Manager → Order Executor
                           (Trading Director: bucle de eventos)
```

Incluye, además del framework del curso:

- Límites de riesgo: pérdida diaria máxima, nº de posiciones, spread y horario sin operar.
- SL/TP en puntos fijos o por ATR.
- Reconexión automática con MT5.
- Diario de operaciones (`logs/trades.csv`).
- Avisos por Telegram de cada cierre y un resumen diario.
- Backtest con los mismos módulos.
- Tests automáticos.

## Requisitos

- **Windows** con MetaTrader 5 instalado. La librería `MetaTrader5` solo existe para Windows.
  El backtest y los tests funcionan también en Mac y Linux.
- [uv](https://docs.astral.sh/uv/). Instala por su cuenta Python 3.11 y las dependencias:
  ```
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

## Instalación

```
git clone https://github.com/Ngmalquin123/mt5-framework.git
cd mt5-framework
uv sync
```

Copia `.env.example` a `.env` y rellena tus datos. El `.env` **nunca** se sube a GitHub.

```
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=...
MT5_SERVER=RoboForex-Pro
MT5_TIMEOUT=60000
MT5_PORTABLE=False
TELEGRAM_TOKEN=          # opcional: sin token no se envían avisos
TELEGRAM_CHAT_ID=
```

En MT5, activa el botón **Trading algorítmico**.

## Configuración: `config.toml`

Todos los parámetros están en `config.toml`, y lo leen tanto el trading real como el backtest.
Así lo que pruebas es exactamente lo que se ejecuta.

| Sección | Qué contiene |
|---|---|
| `[trading]` | símbolos, timeframe, magic number y estrategia activa (`rsi`, `rsi_trend`, `ma_crossover` o `session_breakout`) |
| `[strategies.*]` | parámetros de cada estrategia y su SL/TP |
| `[sizing]` | volumen fijo, mínimo o por % de riesgo |
| `[risk]` / `[limits]` | apalancamiento máximo y límites generales |
| `[orders]` / `[journal]` | caducidad de las órdenes pendientes y archivo del diario |
| `[backtest]` | periodo, fecha de corte práctica/comprobación (`split`), balance inicial, comisión y spread |

**Estrategias:**
- `rsi`: RSI de reversión a la media.
- `rsi_trend`: RSI que solo compra por encima de una media larga y solo vende por debajo, es decir, solo a favor de la tendencia.
- `ma_crossover`: cruce de medias.
- `session_breakout`: ruptura del rango de la sesión asiática en la apertura de Londres (horas del servidor).
  Como mucho una operación por par y día; cierra a una hora fija.

**SL/TP por ATR.** Con `sl_atr = 1.5` y `tp_atr = 3.0`, los stops se ponen a 1.5 y 3 veces el
rango medio de las velas. Se adaptan solos al timeframe, al símbolo y a la volatilidad.
Combínalo con `method = "risk_pct"` para que el volumen se ajuste y cada operación arriesgue
el mismo % de la cuenta.

Un parámetro mal escrito, por ejemplo `sl_pionts`, da error al arrancar en lugar de ignorarse.

## Uso

**Trading en la cuenta** (demo o real, según el `.env`):
```
uv run trading_app.py
```
Se detiene con `Ctrl+C`. Los mensajes se guardan también en `logs/trading_FECHA.log`,
y las operaciones cerradas en `logs/trades.csv`.

**Backtest:**
```
# 1) Descargar datos (Windows con MT5 abierto). Usa el mismo timeframe que config.toml
uv run python -m backtest.download_data --symbols EURUSD USDJPY GBPUSD --timeframe 1min --start 2026-08-01

# 2) Ejecutar
uv run backtest_app.py
uv run backtest_app.py pruebas.toml      # otra configuración, sin tocar la del real
```
El informe se muestra al terminar. Las operaciones y la curva de equity se guardan en `backtest/results/`.

**Pruebas en lote.** Lanza la misma configuración en varios timeframes, cada uno con un periodo de práctica
(de `start` a `split`) y otro de comprobación (de `split` a `end`), y muestra una tabla resumen:
```
uv run python -m backtest.batch pruebas.toml --timeframes 5min 15min 30min 1h
```
Necesita los datos descargados de cada timeframe; la tabla indica cuáles faltan.

Cómo simula el precio:
- SL y TP se comprueban con el máximo y el mínimo de cada vela.
  Si en una misma vela se tocan los dos, se cuenta como SL.
- No simula swaps ni margen.
- MT5 solo descarga tantas velas como «Máx. barras en el gráfico» (Herramientas > Opciones > Gráficos).

Para no adaptar los parámetros al pasado, elígelos con un periodo y compruébalos después en otro distinto.

**Comprobar los datos del broker** (zona horaria del servidor, y que las velas M15/H1 construidas a partir de M5
coinciden con las de MT5). Necesita M5, M15 y H1 descargados:
```
uv run python -m backtest.check_data --symbols EURUSD GBPUSD
```

**Tests:**
```
uv run pytest
```
Usan un MetaTrader5 simulado y nunca tocan la cuenta.

## Estructura

```
config.toml              parámetros (real y backtest)
trading_app.py           arranque en la cuenta
backtest_app.py          arranque del backtest
trading_director/        bucle principal de eventos, tareas periódicas, avisos
data_provider/           velas y ticks
signal_generator/        estrategias (RSI, cruce de medias) y SL/TP (stop_levels.py)
market_structure/        estructura de mercado: swings, HH/HL/LH/LL, tendencia, BOS y CHoCH; velas M15/H1 desde M5; horas de sesión
position_sizer/          tamaño de las posiciones
risk_manager/            apalancamiento máximo y límites de riesgo
order_executor/          envío y cierre de órdenes
portfolio/               posiciones abiertas de la estrategia
trade_journal/           diario de operaciones y resumen diario
notifications/           Telegram
platform_connector/      conexión con MT5
backtest/                MT5 simulado, motor del backtest, pruebas en lote y descarga de datos
tests/                   tests automáticos (con MT5 simulado)
```

## Dejarlo funcionando 24/5 (VPS)

Para que opere sin tener tu PC encendido, usa un **VPS con Windows**. Lo ofrece el propio broker,
o proveedores como AWS, Contabo o ForexVPS; conviene que esté cerca del servidor del broker.
Instala allí MT5, git y uv, clona el repositorio, crea el `.env` y ejecuta `uv run trading_app.py`.

- Desactiva las actualizaciones automáticas con reinicio de Windows.
- Configura el framework para que arranque al iniciar sesión (Programador de tareas).
- Con Telegram configurado recibirás los avisos de conexión perdida, errores y cierres.
