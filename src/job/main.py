#!/usr/bin/env python3
"""
Cloud Run Job: cada 15 min.
1. Lee últimos 50 registros de BigQuery (warm-up / Data Lake como fuente de verdad).
2. Obtiene klines 15m de Binance (BTCUSDT).
3. Calcula RSI(14), EMA(9), EMA(21) y señal (BUY/SELL/NEUTRAL).
4. Publica una fila por mensaje a Pub/Sub (el suscriptor inserta en BQ y alerta Discord).

Logs a stdout para Cloud Logging; errores manejados y registrados con contexto.
"""
import os
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests
from google.cloud import bigquery, pubsub_v1

try:
    import pandas_ta as ta
except ImportError:
    ta = None

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


PROJECT_ID = os.environ["PROJECT_ID"]
PUBSUB_TOPIC = os.environ["PUBSUB_TOPIC"]
BQ_DATASET = os.environ["BQ_DATASET"]
BQ_TABLE = os.environ["BQ_TABLE"]
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
BQ_WARMUP_LIMIT = 50
VOLUME_AVG_WINDOW = 20  # últimas 20 velas de 15m para avg_volume
BINANCE_KLINES_URL = "https://api2.binance.com/api/v3/klines"


def fetch_klines(symbol: str, interval: str, limit: int = 52) -> pd.DataFrame:
    """Obtiene klines de Binance (OHLCV)."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        logger.exception(
            "Binance API request failed: %s",
            e,
            extra={"component": "crypto_ingestion_job", "symbol": symbol},
        )
        raise
    data = r.json()
    # [open_time, open, high, low, close, volume, ...]
    df = pd.DataFrame(
        data,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
        ],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def fetch_bq_warmup(project: str, dataset: str, table: str, limit: int) -> pd.DataFrame:
    """Lee los últimos `limit` registros de BigQuery (warm-up). Idempotente (solo lectura)."""
    client = bigquery.Client(project=project)
    full_table = f"{project}.{dataset}.{table}"
    query = f"""
    SELECT timestamp, open_price AS open, high_price AS high, low_price AS low,
           close_price AS close, volume
    FROM `{full_table}`
    ORDER BY timestamp DESC
    LIMIT {limit}
    """
    try:
        df = client.query(query).to_dataframe()
    except Exception as e:
        logger.warning(
            "BigQuery warm-up failed (table may be empty or missing): %s",
            e,
            extra={"component": "crypto_ingestion_job", "table": full_table},
        )
        raise
    if df.empty:
        return df
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def merge_warmup_and_binance(bq_df: pd.DataFrame, binance_df: pd.DataFrame) -> pd.DataFrame:
    """Combina histórico de BQ con velas nuevas de Binance (sin duplicar por timestamp)."""
    if bq_df.empty:
        return binance_df.copy()
    # Quitar de Binance las filas que ya están en BQ (por timestamp)
    last_bq_ts = bq_df["timestamp"].max()
    new_only = binance_df[binance_df["timestamp"] > last_bq_ts]
    if new_only.empty:
        return bq_df.copy()
    combined = pd.concat([bq_df, new_only], ignore_index=True).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    # Mantener solo los últimos ~50 para no crecer sin control
    return combined.tail(BQ_WARMUP_LIMIT + 5).reset_index(drop=True)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Añade RSI(14), EMA(9), EMA(21) con pandas-ta."""
    if ta is None:
        raise RuntimeError("pandas_ta no instalado")
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.ema(length=9, append=True)
    df.ta.ema(length=21, append=True)
    return df


def compute_signal_with_context(df: pd.DataFrame, last_idx: int) -> str:
    """
    Señal por reversión y tendencia:
    - BUY: RSI bajo (sobreventa) + precio recuperando EMA rápida + volumen confirmando.
    - SELL: RSI alto (sobrecompra) o tendencia rota (cruce muerte + precio por debajo de EMA rápida).
    - avg_volume sobre las últimas VOLUME_AVG_WINDOW velas de 15m.
    """
    row = df.iloc[last_idx]
    rsi = row.get("RSI_14")
    ema_9 = row.get("EMA_9")
    ema_21 = row.get("EMA_21")
    close = row["close"]
    volume = row["volume"]
    if pd.isna(rsi) or pd.isna(ema_9) or pd.isna(ema_21):
        return "NEUTRAL"
    # Ventana representativa: últimas VOLUME_AVG_WINDOW velas de 15m
    start = max(0, last_idx - (VOLUME_AVG_WINDOW - 1))
    avg_volume = df.iloc[start : last_idx + 1]["volume"].mean()
    volume_ok = volume >= (avg_volume * 1.2) if avg_volume and avg_volume > 0 else False
    oversold = rsi < 35
    overbought = rsi > 70
    # BUY: rebote — RSI bajo + precio por encima de EMA rápida + volumen confirmando
    if oversold and close > ema_9 and volume_ok:
        return "BUY"
    # SELL: euforia (RSI > 70)
    if overbought:
        return "SELL"
    # SELL: tendencia rota — cruce muerte y precio por debajo de EMA rápida
    if (ema_9 < ema_21) and (close < ema_9):
        return "SELL"
    return "NEUTRAL"


def row_to_payload(row: pd.Series, symbol: str, signal: str) -> dict:
    """Convierte una fila del DataFrame a payload para Pub/Sub y BigQuery."""
    ts = row["timestamp"]
    if hasattr(ts, "isoformat"):
        ts_str = ts.isoformat()
    else:
        ts_str = pd.Timestamp(ts).isoformat()
    return {
        "timestamp": ts_str,
        "symbol": symbol,
        "open_price": float(row["open"]),
        "high_price": float(row["high"]),
        "low_price": float(row["low"]),
        "close_price": float(row["close"]),
        "volume": float(row["volume"]),
        "rsi": float(row["RSI_14"]) if pd.notna(row.get("RSI_14")) else None,
        "ema_9": float(row["EMA_9"]) if pd.notna(row.get("EMA_9")) else None,
        "ema_21": float(row["EMA_21"]) if pd.notna(row.get("EMA_21")) else None,
        "signal": signal,
    }


def publish_to_pubsub(project_id: str, topic_id: str, payload: dict) -> None:
    """Publica un mensaje en Pub/Sub."""
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_id)
    data = json.dumps(payload).encode("utf-8")
    try:
        future = publisher.publish(topic_path, data)
        future.result(timeout=30)
    except Exception as e:
        logger.exception(
            "Pub/Sub publish failed: %s",
            e,
            extra={"component": "crypto_ingestion_job", "topic": topic_path},
        )
        raise
    logger.info(
        "Message published to Pub/Sub",
        extra={"component": "crypto_ingestion_job", "topic": topic_path},
    )


def run() -> None:
    """Orquestación del job: warm-up BQ, Binance, indicadores, publicación Pub/Sub."""
    logger.info(
        "Job started: warm-up BQ + Binance + indicators + Pub/Sub",
        extra={"component": "crypto_ingestion_job"},
    )
    project = PROJECT_ID
    # 1) Warm-up desde BigQuery (fallo no fatal: continuar sin histórico)
    try:
        bq_df = fetch_bq_warmup(project, BQ_DATASET, BQ_TABLE, BQ_WARMUP_LIMIT)
        logger.info(
            "BigQuery warm-up rows: %d",
            len(bq_df),
            extra={"component": "crypto_ingestion_job", "row_count": len(bq_df)},
        )
    except Exception as e:
        logger.warning(
            "BigQuery warm-up failed, continuing without history: %s",
            e,
            extra={"component": "crypto_ingestion_job"},
        )
        bq_df = pd.DataFrame()

    # 2) Klines Binance
    binance_df = fetch_klines(SYMBOL, INTERVAL, limit=52)
    logger.info(
        "Binance klines: %d",
        len(binance_df),
        extra={"component": "crypto_ingestion_job", "count": len(binance_df)},
    )

    # 3) Combinar
    df = merge_warmup_and_binance(bq_df, binance_df)
    if df.empty:
        logger.error(
            "No data to process after merge",
            extra={"component": "crypto_ingestion_job"},
        )
        return

    # 4) Indicadores
    df = compute_indicators(df)
    last_idx = len(df) - 1
    signal = compute_signal_with_context(df, last_idx)
    row = df.iloc[last_idx]
    payload = row_to_payload(row, SYMBOL, signal)
    payload["ingestion_at"] = datetime.now(timezone.utc).isoformat()

    # 5) Publicar a Pub/Sub (el suscriptor insertará en BQ y enviará Discord si aplica)
    publish_to_pubsub(project, PUBSUB_TOPIC, payload)
    logger.info(
        "Job completed successfully",
        extra={"component": "crypto_ingestion_job", "signal": signal},
    )


if __name__ == "__main__":
    run()
