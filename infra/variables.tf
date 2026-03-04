# =============================================================================
# Variables - Real-Time Crypto Monitor (Free Tier GCP)
# =============================================================================

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region para la mayoría de recursos (BigQuery, Pub/Sub, subscriber). us-central1 = Free Tier."
  type        = string
  default     = "us-central1"
}

# Región donde corre el Job de ingesta (Binance API). Binance devuelve 451 desde US; EU suele estar permitido.
variable "job_region" {
  description = "Región del Cloud Run Job (y del Scheduler). europe-west1 = Free Tier y evita bloqueo Binance desde EU."
  type        = string
  default     = "europe-west1"
}

# Discord webhook (opcional; si está vacío no se envían alertas a Discord)
variable "discord_webhook_url" {
  description = "URL del webhook de Discord para alertas (dejar vacío para desactivar)"
  type        = string
  default     = ""
  sensitive   = true
}

# Imágenes de Cloud Run — placeholders para el primer apply (esqueleto); luego URL real
variable "subscriber_image" {
  description = "Imagen Docker del suscriptor (Cloud Run service). Primer apply: placeholder; después: URL real de Artifact Registry."
  type        = string
  default     = "gcr.io/cloudrun/hello"
}

variable "job_image" {
  description = "Imagen Docker del Job de ingesta. Primer apply: placeholder; después: URL real de Artifact Registry."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job:latest"
}
