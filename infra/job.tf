# =============================================================================
# Cloud Run Job + Cloud Scheduler (cada 15 minutos)
# =============================================================================

# Job en job_region (ej. europe-west1) para evitar HTTP 451 de Binance desde US
resource "google_cloud_run_v2_job" "crypto_ingestion" {
  name     = "crypto-ingestion-job"
  location = var.job_region
  project  = var.project_id

  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.job_sa.email
      containers {
        image = var.job_image
        #image = "gcr.io/cloudrun/hello"
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "PUBSUB_TOPIC"
          value = google_pubsub_topic.crypto_prices.name
        }
        env {
          name  = "BQ_DATASET"
          value = google_bigquery_dataset.crypto_analytics.dataset_id
        }
        env {
          name  = "BQ_TABLE"
          value = google_bigquery_table.market_indicators.table_id
        }
        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }
      }
      max_retries = 2
      timeout     = "300s" # Límite duro: 300 segundos
    }
  }

  depends_on = [
    google_project_service.run,
  ]
}

# Scheduler en la misma región que el Job (obligatorio para invocar :run)
resource "google_cloud_scheduler_job" "crypto_job" {
  name             = "crypto-ingestion-every-15min"
  schedule         = "*/15 * * * *"
  time_zone        = "UTC"
  attempt_deadline = "600s"
  project          = var.project_id
  region           = var.job_region

  http_target {
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.job_region}/jobs/${google_cloud_run_v2_job.crypto_ingestion.name}:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.job_sa.email
    }
  }

  depends_on = [
    google_project_service.scheduler,
  ]
}
