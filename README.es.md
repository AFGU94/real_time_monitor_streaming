# Real-Time Crypto/Market Monitor (Streaming en GCP Free Tier)

Sistema de monitorización de Bitcoin en tiempo (casi) real sobre **Google Cloud (Free Tier)**:

- **Origen:** Binance API (velas 15m BTCUSDT).
- **Ingesta:** Pub/Sub.
- **Procesado:** Cloud Run Job cada 15 min (warm-up desde BigQuery, indicadores con pandas-ta, publicación a Pub/Sub) + Cloud Run suscriptor (inserción BigQuery, alertas Discord con hysteresis en Firestore).
- **Almacenamiento:** BigQuery `crypto_analytics.market_indicators` (particionada por día, clustering por symbol).
- **Alertas:** Bot de Discord con lógica de estado en Firestore (última señal, precio, RSI) y umbral de mejora (refuerzos de compra/venta); evita spam y solo avisa cuando tiene sentido actuar.
- **Coste:** Crea a mano una alerta de presupuesto (p. ej. $1 USD) en **Billing → Budgets**.

---
## Flujo de datos

De forma resumida, el flujo es:

Binance API (15m klines)
        │
        ▼
Cloud Run Job (cada 15 min)
   • Lee últimos 50 registros de BigQuery (warm-up)
   • Calcula RSI, EMA9, EMA21, señal (BUY/SELL/NEUTRAL)
   • Publica 1 mensaje por vela → Pub/Sub
        │
        ▼
Pub/Sub (topic: crypto-prices)
        │ push
        ▼
Cloud Run (subscriber)
   • Inserta fila en BigQuery (streaming)
   • Si signal=(logica de estado, checked conditions) → Discord + Firestore
        │
        ▼
BigQuery: crypto_analytics.market_indicators
   (particionada por día, clustering por symbol)

- **1. Binance API (15m klines)**  
  El Job en Cloud Run llama a Binance cada 15 minutos para obtener velas de 15m de `BTCUSDT` (OHLCV).

- **2. Cloud Run Job (cada 15 min)**  
  - Lee últimos registros de BigQuery (warm-up, opcional).  
  - Combina histórico + velas nuevas y calcula indicadores técnicos (RSI(14), EMA(9), EMA(21)).  
  - Calcula una señal por vela: `BUY`, `SELL` o `NEUTRAL`.  
  - Publica **un mensaje JSON por vela** en Pub/Sub con todos los campos (precio, volumen, indicadores, señal).

- **3. Pub/Sub (topic `crypto-prices`)**  
  - El Job publica en el topic `crypto-prices`.  
  - Una suscripción push envía cada mensaje al servicio Cloud Run suscriptor.

- **4. Cloud Run (subscriber)**  
  - Recibe el push de Pub/Sub, decodifica el JSON.  
  - Inserta una fila en BigQuery (streaming insert) en la tabla `crypto_analytics.market_indicators`.  
  - Evalúa la señal (`BUY`/`SELL`) junto con el **estado almacenado en Firestore** para decidir si envía o no una alerta a Discord.

- **5. BigQuery: `crypto_analytics.market_indicators`**  
  - Tabla particionada por día, con clustering por `symbol`.  
  - Cada fila representa una vela de 15 min con sus indicadores y la señal final.  
  - Las particiones antiguas se borran automáticamente pasado un tiempo para mantenerse dentro del Free Tier.

En paralelo, **Firestore** almacena el último estado de alerta (`signal`, `price`, `timestamp`, `rsi`, `expireAt`) y **Discord** recibe las alertas en forma de embeds (compra/venta/refuerzos).

---
## Alertas - lógica de estado (Cloud Run Suscriptor)

El comportamiento de alertas está implementado en `src/subscriber/main.py` (función `maybe_send_alert`) y se apoya en Firestore:

- **Estado en Firestore**  
  - Se guarda un documento `crypto_alerts/last_alert` con:  
    - `signal`: última señal enviada (`BUY` o `SELL`).  
    - `price`: precio de referencia de esa señal.  
    - `rsi`: RSI en el momento de la señal.  
    - `timestamp`: cuándo se guardó.  
    - `expireAt`: fecha a partir de la cual Firestore borra el documento (TTL, p. ej. 30 días).

- **Señal BUY**  
  - Si la señal actual es `BUY` y la última señal **no** fue `BUY` (era `SELL`, `NEUTRAL` o no había estado):  
    - Envía un embed verde a Discord tipo **“Oportunidad de compra”** (par, precio actual, RSI).  
    - Actualiza Firestore con `signal=BUY`, `price=precio_actual`, `rsi=RSI_actual`.  
  - Si la señal actual es `BUY` y la última también fue `BUY`:  
    - Si el precio actual es **al menos un 5 % más bajo** que el `price` guardado (`precio_actual < price * 0.95`):  
      - Envía un embed de **refuerzo de compra**: *“Refuerzo de compra: mejor precio detectado (oportunidad de promediar)”*.  
      - Actualiza Firestore con el nuevo `price` (nuevo nivel de referencia).  
    - Si no hay mejora ≥ 5 %, **no envía nada** (evita spam mientras el mercado sigue en la misma zona).

- **Señal SELL**  
  - Si la señal actual es `SELL` y la última señal en Firestore fue `BUY`:  
    - Calcula la **ganancia hipotética** entre el `price` guardado (precio de compra) y el precio actual.  
    - Envía un embed rojo de venta con: precio de compra, precio actual y % de ganancia/pérdida.  
    - Actualiza Firestore con `signal=SELL` y `price=precio_actual`.  
  - Si la señal actual es `SELL` y la última señal fue `SELL`:  
    - Si el precio actual es **al menos un 5 % más alto** que el `price` guardado (`precio_actual > price * 1.05`):  
      - Envía un embed de **refuerzo de venta**: *“Refuerzo de venta: mejor precio detectado (mejor salida)”*.  
      - Actualiza Firestore con el nuevo `price`.  
    - Si no hay mejora ≥ 5 %, no envía nada (no spamea múltiples SELL casi iguales).  
  - Si la última señal no fue ni `BUY` ni `SELL` (estado nulo/inconsistente), ignora la señal `SELL` (no vende algo que “no compró” antes).

En resumen, el bot **recuerda lo que hizo antes** y solo envía alertas cuando:

- Cambia de estado relevante (por ejemplo, de NEUTRAL/SELL a BUY, o de BUY a SELL), o  
- El mercado ofrece **un precio significativamente mejor (±5 %)** para reforzar una decisión anterior (más barato para comprar, mejor precio para vender).

---
## Guía paso a paso

1. **Terraform con placeholders** de imagen (Job: `us-docker.pkg.dev/cloudrun/container/job:latest`, Service: `gcr.io/cloudrun/hello`).
2. **Apply** → montar infraestructura (esqueleto).
3. **Verificación** en consola GCP (Cloud Run verde).
4. **Desarrollo** y prueba local de scripts Python.
5. **Build y push** de imágenes reales a Artifact Registry.
6. **Update** → cambiar variables de imagen en Terraform y `terraform apply` de nuevo.

---
## Estructura

- `infra/` – IaC Terraform (Pub/Sub, BigQuery, Cloud Run, Scheduler). Recursos en código; presupuesto/alertas en Billing a mano.
- `src/` – Código Python de ingesta (job + subscriber); usar `.venv` en la raíz.
- `src/job/` – Cloud Run Job: Binance + BQ warm-up + RSI/EMA + Pub/Sub.
- `src/subscriber/` – Cloud Run Service: push Pub/Sub → BigQuery + Firestore + Discord.
- `dbt_project/` – Reservado para modelos dbt (ver convenciones en `.cursor/rules/`).

---
## Convenciones

- **IaC**: recursos en `infra/` (Terraform); región `us-central1` (Always Free Tier).
- **Código**: Python modular, con type hints y PEP 8; errores manejados y logs a stdout (Cloud Logging); dependencias fijadas en `requirements.txt`.
- **Estructura**: `infra/`, `src/`, `dbt_project/` (reservado).

---
## Requisitos

- Python 3.12+, Docker, `gcloud`, Terraform ≥ 1.0.
- Cuenta GCP con facturación (para presupuesto y uso dentro del free tier).
