# =============================================================================
# BigQuery - Dataset y tabla particionada + clustering
# =============================================================================

resource "google_bigquery_dataset" "crypto_analytics" {
  dataset_id = "crypto_analytics"
  location   = var.region
  description = "Dataset para análisis de mercado crypto en tiempo real"
}

# 30 días en milisegundos (para expiración y mantener Free Tier)
locals {
  bigquery_expiration_ms = 30 * 24 * 60 * 60 * 1000 # 2592000000
}

# Tabla market_indicators: particionada por día, clustering por symbol
resource "google_bigquery_table" "market_indicators" {
  dataset_id = google_bigquery_dataset.crypto_analytics.dataset_id
  table_id   = "market_indicators"

  description = "Indicadores de mercado (RSI, EMA, volumen) por vela de 15 min. Clave lógica: (symbol, timestamp); para dedup: QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol, timestamp ORDER BY ingestion_at DESC)=1"

  require_partition_filter = false

  time_partitioning {
    type          = "DAY"
    field         = "timestamp"
    expiration_ms = local.bigquery_expiration_ms # Borra particiones con más de 30 días
  }

  clustering = ["symbol"]

  schema = jsonencode([
    { name = "timestamp",   type = "TIMESTAMP", mode = "REQUIRED", description = "Cierre de la vela 15m" },
    { name = "symbol",      type = "STRING",    mode = "REQUIRED", description = "Par (ej. BTCUSDT)" },
    { name = "open_price",  type = "FLOAT64",   mode = "REQUIRED" },
    { name = "high_price",  type = "FLOAT64",   mode = "REQUIRED" },
    { name = "low_price",   type = "FLOAT64",   mode = "REQUIRED" },
    { name = "close_price", type = "FLOAT64",   mode = "REQUIRED" },
    { name = "volume",      type = "FLOAT64",   mode = "REQUIRED" },
    { name = "rsi",         type = "FLOAT64",   mode = "NULLABLE" },
    { name = "ema_9",       type = "FLOAT64",   mode = "NULLABLE" },
    { name = "ema_21",      type = "FLOAT64",   mode = "NULLABLE" },
    { name = "signal",      type = "STRING",    mode = "REQUIRED", description = "BUY, SELL o NEUTRAL" },
    { name = "ingestion_at", type = "TIMESTAMP", mode = "REQUIRED", description = "Cuándo se insertó en BQ" }
  ])
}
