# Real-Time Crypto/Market Monitor (Streaming in GCP Free Tier)
**This document is also available in [Spanish](README.es.md).**

Near real-time Bitcoin monitoring on **Google Cloud (Free Tier)**:

- **Source:** Binance API (15m candles, BTCUSDT).
- **Ingestion:** Pub/Sub.
- **Processing:** Cloud Run Job every 15 min (BigQuery warm-up, indicators via pandas-ta, publish to Pub/Sub) + Cloud Run subscriber (BigQuery insert, Discord alerts with Firestore hysteresis).
- **Storage:** BigQuery `crypto_analytics.market_indicators` (day-partitioned, clustered by symbol).
- **Alerts:** Discord bot with state logic in Firestore (last signal, price, RSI) and an improvement threshold (buy/sell reinforcements); avoids spam and only notifies when it makes sense to act.
- **Cost:** Set up a budget alert (e.g. $1 USD) manually under **Billing → Budgets**.

---
## Data flow

In short, the flow is:

```
Binance API (15m klines)
        │
        ▼
Cloud Run Job (every 15 min)
   • Reads last 50 rows from BigQuery (warm-up)
   • Computes RSI, EMA9, EMA21, signal (BUY/SELL/NEUTRAL)
   • Publishes 1 message per candle → Pub/Sub
        │
        ▼
Pub/Sub (topic: crypto-prices)
        │ push
        ▼
Cloud Run (subscriber)
   • Inserts row into BigQuery (streaming)
   • If signal=(state logic, conditions met) → Discord + Firestore
        │
        ▼
BigQuery: crypto_analytics.market_indicators
   (day-partitioned, clustered by symbol)
```

- **1. Binance API (15m klines)**  
  The Cloud Run Job calls Binance every 15 minutes to fetch 15m candles for `BTCUSDT` (OHLCV).

- **2. Cloud Run Job (every 15 min)**  
  - Reads the latest rows from BigQuery (warm-up, optional).  
  - Merges history with new candles and computes technical indicators (RSI(14), EMA(9), EMA(21)).  
  - Computes one signal per candle: `BUY`, `SELL`, or `NEUTRAL`.  
  - Publishes **one JSON message per candle** to Pub/Sub with all fields (price, volume, indicators, signal).

- **3. Pub/Sub (topic `crypto-prices`)**  
  - The Job publishes to the `crypto-prices` topic.  
  - A push subscription delivers each message to the Cloud Run subscriber service.

- **4. Cloud Run (subscriber)**  
  - Receives the Pub/Sub push, decodes the JSON.  
  - Inserts one row into BigQuery (streaming insert) in the `crypto_analytics.market_indicators` table.  
  - Evaluates the signal (`BUY`/`SELL`) together with **state stored in Firestore** to decide whether to send a Discord alert.

- **5. BigQuery: `crypto_analytics.market_indicators`**  
  - Table partitioned by day, clustered by `symbol`.  
  - Each row represents one 15m candle with its indicators and final signal.  
  - Older partitions are dropped automatically after a set period to stay within the Free Tier.

In parallel, **Firestore** stores the latest alert state (`signal`, `price`, `timestamp`, `rsi`, `expireAt`), and **Discord** receives alerts as embeds (buy/sell/reinforcements).

---
## Alerts state logic (Cloud Run Subscriber)

Alert behaviour is implemented in `src/subscriber/main.py` (function `maybe_send_alert`) and relies on Firestore:

- **State in Firestore**  
  - A document `crypto_alerts/last_alert` is stored with:  
    - `signal`: last sent signal (`BUY` or `SELL`).  
    - `price`: reference price for that signal.  
    - `rsi`: RSI at the time of the signal.  
    - `timestamp`: when it was stored.  
    - `expireAt`: time after which Firestore deletes the document (TTL, e.g. 30 days).

- **BUY signal**  
  - If the current signal is `BUY` and the last signal was **not** `BUY` (it was `SELL`, `NEUTRAL`, or missing):  
    - Sends a green Discord embed **“Buy opportunity”** (pair, current price, RSI).  
    - Updates Firestore with `signal=BUY`, `price=current_price`, `rsi=current_rsi`.  
  - If the current signal is `BUY` and the last was also `BUY`:  
    - If the current price is **at least 5% lower** than the stored `price` (`current_price < price * 0.95`):  
      - Sends a **buy reinforcement** embed: *“Reinforced buy: better price detected (opportunity to average in)”*.  
      - Updates Firestore with the new `price` (new reference level).  
    - If there is no ≥5% improvement, **sends nothing** (avoids spam while the market stays in the same zone).

- **SELL signal**  
  - If the current signal is `SELL` and the last signal in Firestore was `BUY`:  
    - Computes **hypothetical profit** between the stored `price` (entry price) and the current price.  
    - Sends a red sell embed with: entry price, current price, and % profit/loss.  
    - Updates Firestore with `signal=SELL` and `price=current_price`.  
  - If the current signal is `SELL` and the last signal was `SELL`:  
    - If the current price is **at least 5% higher** than the stored `price` (`current_price > price * 1.05`):  
      - Sends a **sell reinforcement** embed: *“Reinforced sell: better price detected (better exit)”*.  
      - Updates Firestore with the new `price`.  
    - If there is no ≥5% improvement, sends nothing (avoids spamming near-identical SELLs).  
  - If the last signal was neither `BUY` nor `SELL` (null or inconsistent state), the bot ignores the `SELL` signal (does not “sell” something it never “bought”).

In summary, the bot **remembers what it did before** and only sends alerts when:

- The state changes in a meaningful way (e.g. from NEUTRAL/SELL to BUY, or from BUY to SELL), or  
- The market offers a **significantly better price (±5%)** to reinforce a previous decision (cheaper to buy, better price to sell).

---
## Step-by-step guide

1. **Terraform with placeholder images** (Job: `us-docker.pkg.dev/cloudrun/container/job:latest`, Service: `gcr.io/cloudrun/hello`).
2. **Apply** → create infrastructure (skeleton).
3. **Verify** in the GCP console (Cloud Run up).
4. **Develop** and test Python scripts locally.
5. **Build and push** real images to Artifact Registry.
6. **Update** → set image variables in Terraform and run `terraform apply` again.

---
## Structure

- `infra/` – Terraform IaC (Pub/Sub, BigQuery, Cloud Run, Scheduler). Resources in code; budget/alerts in Billing are set manually.
- `src/` – Python ingestion code (job + subscriber); use `.venv` at the repo root.
- `src/job/` – Cloud Run Job: Binance + BQ warm-up + RSI/EMA + Pub/Sub.
- `src/subscriber/` – Cloud Run Service: Pub/Sub push → BigQuery + Firestore + Discord.
- `dbt_project/` – Reserved for dbt models (see conventions in `.cursor/rules/`).

---
## Requirements

- Python 3.12+, Docker, `gcloud`, Terraform ≥ 1.0.
- A GCP account with billing enabled (for budget and usage within the free tier).
