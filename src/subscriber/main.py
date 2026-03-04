#!/usr/bin/env python3
"""
Cloud Run Suscriptor: recibe push de Pub/Sub.
1. Parsea el mensaje (payload con timestamp, symbol, OHLCV, rsi, ema_9, ema_21, signal).
2. Inserta una fila en BigQuery (streaming insert).
3. Alertas Discord con estado en Firestore (objeto: signal, price, timestamp, rsi):
   - BUY: si última fue NEUTRAL/SELL → "Oportunidad de compra". Si última fue BUY y precio actual 5% menor → "Refuerzo de compra: mejor precio".
   - SELL: solo si última fue BUY (ganancia hipotética). Si última fue SELL y precio actual 5% mayor → "Refuerzo de venta: mejor precio".

Logs a stderr para que en Cloud Run aparezcan junto a gunicorn (run.googleapis.com/stderr).
Breadcrumbs al inicio y tras cada paso del handler para localizar dónde se cuelga.
"""
import base64
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from flask import Flask, request
from google.cloud import bigquery, firestore
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

BQ_PROJECT = os.environ.get("BQ_PROJECT", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "crypto_analytics")
BQ_TABLE = os.environ.get("BQ_TABLE", "market_indicators")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
FIRESTORE_COLLECTION = "crypto_alerts"
FIRESTORE_DOC_LAST_ALERT = "last_alert"
# Umbral de mejora: 5% mejor que la última alerta para enviar "refuerzo"
IMPROVEMENT_THRESHOLD = 0.05


def parse_pubsub_message() -> Optional[dict]:
    """Extrae el payload JSON del body de Pub/Sub push."""
    body = request.get_json(silent=True)
    if not body or "message" not in body:
        return None
    msg = body["message"]
    data = msg.get("data")
    if not data:
        return None
    raw = base64.b64decode(data).decode("utf-8")
    return json.loads(raw)


def insert_bigquery(project: str, dataset: str, table: str, row: dict) -> None:
    """Streaming insert de una fila en BigQuery."""
    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table}"
    ts = row.get("timestamp")
    if isinstance(ts, str):
        ts = ts.replace("Z", "+00:00")
    ingestion = datetime.now(timezone.utc).isoformat()
    bq_row = {
        "timestamp": ts,
        "symbol": row["symbol"],
        "open_price": row["open_price"],
        "high_price": row["high_price"],
        "low_price": row["low_price"],
        "close_price": row["close_price"],
        "volume": row["volume"],
        "rsi": row.get("rsi"),
        "ema_9": row.get("ema_9"),
        "ema_21": row.get("ema_21"),
        "signal": row["signal"],
        "ingestion_at": ingestion,
    }
    errors = client.insert_rows_json(table_ref, [bq_row])
    if errors:
        logger.error(
            "BigQuery insert failed: %s",
            errors,
            extra={"component": "crypto_subscriber", "table": table_ref},
        )
        raise RuntimeError(f"BigQuery insert failed: {errors}")
    logger.info(
        "Row inserted into BigQuery",
        extra={
            "component": "crypto_subscriber",
            "symbol": row["symbol"],
            "timestamp": row.get("timestamp"),
        },
    )


def get_last_alert() -> Optional[dict[str, Any]]:
    """
    Devuelve el último estado de alerta desde Firestore.
    Esquema: signal, price, timestamp (updated_at), rsi.
    Compatible con docs antiguos que usan close_price en lugar de price.
    """
    try:
        db = firestore.Client()
        doc = db.collection(FIRESTORE_COLLECTION).document(FIRESTORE_DOC_LAST_ALERT).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.warning(
            "Firestore get_last_alert failed: %s",
            e,
            extra={"component": "crypto_subscriber"},
        )
        return None


def _last_price(last: Optional[dict[str, Any]]) -> Optional[float]:
    """Precio de la última alerta (price o close_price por compatibilidad)."""
    if not last:
        return None
    return last.get("price") if last.get("price") is not None else last.get("close_price")


# TTL Firestore: documentos expiran a los 30 días (Free Tier)
FIRESTORE_TTL_DAYS = 30


def set_last_alert(
    signal: str,
    price: Optional[float] = None,
    rsi: Optional[float] = None,
) -> None:
    """Guarda en Firestore el estado: signal, price, timestamp, rsi. Incluye expireAt para TTL (30 días)."""
    try:
        db = firestore.Client()
        now = datetime.now(timezone.utc)
        data: dict[str, Any] = {
            "signal": signal,
            "timestamp": now.isoformat(),
            "expireAt": now + timedelta(days=FIRESTORE_TTL_DAYS),  # TTL: Firestore borra el doc tras 30 días
        }
        if price is not None:
            data["price"] = price
        if rsi is not None:
            data["rsi"] = rsi
        db.collection(FIRESTORE_COLLECTION).document(FIRESTORE_DOC_LAST_ALERT).set(data)
    except Exception as e:
        logger.warning(
            "Firestore set_last_alert failed: %s",
            e,
            extra={"component": "crypto_subscriber"},
        )


# Colores para embeds de Discord (decimal)
DISCORD_COLOR_GREEN = 0x00FF00   # BUY
DISCORD_COLOR_RED = 0xFF0000     # SELL


def _build_embed_buy(payload: dict) -> dict:
    """Embed verde: oportunidad de compra con precio y RSI."""
    close_price = payload.get("close_price") or 0
    rsi = payload.get("rsi")
    symbol = payload.get("symbol", "BTCUSDT")
    rsi_str = f"{rsi:.1f}" if rsi is not None and isinstance(rsi, (int, float)) else "N/A"
    return {
        "title": "Oportunidad de compra",
        "color": DISCORD_COLOR_GREEN,
        "fields": [
            {"name": "Par", "value": symbol, "inline": True},
            {"name": "Precio actual", "value": f"{close_price:,.2f}", "inline": True},
            {"name": "RSI", "value": rsi_str, "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_embed_sell(payload: dict, last_buy_price: float) -> dict:
    """Embed rojo: señal de venta con ganancia hipotética desde la última compra."""
    close_price = payload.get("close_price") or 0
    symbol = payload.get("symbol", "BTCUSDT")
    diff = close_price - last_buy_price
    pct = (diff / last_buy_price * 100) if last_buy_price else 0
    return {
        "title": "Señal de venta",
        "color": DISCORD_COLOR_RED,
        "fields": [
            {"name": "Par", "value": symbol, "inline": True},
            {"name": "Precio compra (última alerta)", "value": f"{last_buy_price:,.2f}", "inline": True},
            {"name": "Precio actual", "value": f"{close_price:,.2f}", "inline": True},
            {"name": "Ganancia hipotética", "value": f"{diff:+,.2f} ({pct:+.2f}%)", "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_embed_buy_reinforcement(payload: dict, previous_price: float) -> dict:
    """Embed verde: refuerzo de compra — mejor precio detectado (oportunidad de promediar)."""
    close_price = payload.get("close_price") or 0
    rsi = payload.get("rsi")
    symbol = payload.get("symbol", "BTCUSDT")
    rsi_str = f"{rsi:.1f}" if rsi is not None and isinstance(rsi, (int, float)) else "N/A"
    drop_pct = ((previous_price - close_price) / previous_price * 100) if previous_price else 0
    return {
        "title": "Refuerzo de compra: mejor precio detectado",
        "description": "Oportunidad de promediar.",
        "color": DISCORD_COLOR_GREEN,
        "fields": [
            {"name": "Par", "value": symbol, "inline": True},
            {"name": "Precio anterior (última alerta)", "value": f"{previous_price:,.2f}", "inline": True},
            {"name": "Precio actual", "value": f"{close_price:,.2f}", "inline": True},
            {"name": "Mejora", "value": f"-{drop_pct:.1f}% (más barato)", "inline": False},
            {"name": "RSI", "value": rsi_str, "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_embed_sell_reinforcement(payload: dict, previous_price: float) -> dict:
    """Embed rojo: refuerzo de venta — mejor precio de salida detectado."""
    close_price = payload.get("close_price") or 0
    symbol = payload.get("symbol", "BTCUSDT")
    rise_pct = ((close_price - previous_price) / previous_price * 100) if previous_price else 0
    return {
        "title": "Refuerzo de venta: mejor precio detectado",
        "description": "Precio de venta más favorable.",
        "color": DISCORD_COLOR_RED,
        "fields": [
            {"name": "Par", "value": symbol, "inline": True},
            {"name": "Precio anterior (última alerta)", "value": f"{previous_price:,.2f}", "inline": True},
            {"name": "Precio actual", "value": f"{close_price:,.2f}", "inline": True},
            {"name": "Mejora", "value": f"+{rise_pct:.1f}% (mejor salida)", "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def send_discord_embed(webhook_url: str, embed: dict) -> bool:
    """Envía un embed a Discord (webhook)."""
    if not webhook_url:
        return False
    body = {"embeds": [embed]}
    try:
        r = requests.post(webhook_url, json=body, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning(
            "Discord webhook failed: %s",
            e,
            extra={"component": "crypto_subscriber"},
        )
        return False


def maybe_send_alert(payload: dict) -> None:
    """
    Estado consciente (Firestore con signal, price, timestamp, rsi) + umbral de mejora 5%:
    - BUY: si última fue NEUTRAL/SELL → alerta compra. Si última fue BUY y precio actual < último * 0.95 → refuerzo "mejor precio".
    - SELL: solo si última fue BUY → alerta venta (ganancia). Si última fue SELL y precio actual > último * 1.05 → refuerzo "mejor precio".
    """
    signal = payload.get("signal")
    if signal not in ("BUY", "SELL"):
        return
    last = get_last_alert()
    last_signal = last.get("signal") if last else None
    last_price = _last_price(last)
    current_price = payload.get("close_price") or 0
    current_rsi = payload.get("rsi")

    if signal == "BUY":
        if last_signal != "BUY":
            # Primera compra o tras SELL/NEUTRAL
            if send_discord_embed(DISCORD_WEBHOOK_URL, _build_embed_buy(payload)):
                set_last_alert("BUY", price=current_price, rsi=current_rsi)
                logger.info(
                    "BUY alert sent to Discord and saved to Firestore",
                    extra={"component": "crypto_subscriber"},
                )
        elif last_price is not None and current_price < (last_price * (1 - IMPROVEMENT_THRESHOLD)):
            # Misma señal BUY pero precio al menos 5% más bajo → refuerzo (oportunidad de promediar)
            if send_discord_embed(
                DISCORD_WEBHOOK_URL,
                _build_embed_buy_reinforcement(payload, float(last_price)),
            ):
                set_last_alert("BUY", price=current_price, rsi=current_rsi)
                logger.info(
                    "BUY reinforcement (better price) sent to Discord and saved to Firestore",
                    extra={"component": "crypto_subscriber"},
                )
        else:
            logger.info(
                "Last alert was BUY and price not 5%% lower; skipping",
                extra={"component": "crypto_subscriber"},
            )

    elif signal == "SELL":
        if last_signal == "BUY":
            # Salida: vendemos lo que habíamos comprado
            last_buy_price = last_price if last_price is not None else current_price
            if send_discord_embed(DISCORD_WEBHOOK_URL, _build_embed_sell(payload, float(last_buy_price))):
                set_last_alert("SELL", price=current_price)
                logger.info(
                    "SELL alert sent to Discord and saved to Firestore",
                    extra={"component": "crypto_subscriber"},
                )
        elif last_signal == "SELL" and last_price is not None and current_price > (
            last_price * (1 + IMPROVEMENT_THRESHOLD)
        ):
            # Ya habíamos avisado SELL pero el precio subió 5%+ → refuerzo (mejor precio de venta)
            if send_discord_embed(
                DISCORD_WEBHOOK_URL,
                _build_embed_sell_reinforcement(payload, float(last_price)),
            ):
                set_last_alert("SELL", price=current_price)
                logger.info(
                    "SELL reinforcement (better price) sent to Discord and saved to Firestore",
                    extra={"component": "crypto_subscriber"},
                )
        else:
            if last_signal != "BUY" and last_signal != "SELL":
                logger.info(
                    "Last alert was not BUY; skipping SELL (nothing to sell)",
                    extra={"component": "crypto_subscriber", "last_signal": last_signal},
                )
            else:
                logger.info(
                    "Last alert was SELL and price not 5%% higher; skipping",
                    extra={"component": "crypto_subscriber"},
                )


@app.route("/", methods=["POST"])
def handle_pubsub_push():
    """Endpoint de push de Pub/Sub."""
    payload = parse_pubsub_message()
    if not payload:
        return "Bad Request", 400
    logger.info("[subscriber] push received signal=%s", payload.get("signal"))
    try:
        logger.info("[subscriber] insert_bigquery starting")
        insert_bigquery(BQ_PROJECT, BQ_DATASET, BQ_TABLE, payload)
        logger.info("[subscriber] insert_bigquery done, maybe_send_alert starting")
        maybe_send_alert(payload)
        logger.info("[subscriber] maybe_send_alert done, returning 204")
        return "", 204
    except Exception as e:
        logger.exception(
            "Processing failed: %s",
            e,
            extra={"component": "crypto_subscriber"},
        )
        return "Internal Server Error", 500


@app.route("/health", methods=["GET"])
def health() -> tuple[str, int]:
    """Health check for Cloud Run."""
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
