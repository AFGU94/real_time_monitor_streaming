# =============================================================================
# Cloud Run - Suscriptor (recibe push de Pub/Sub, escribe BigQuery, Discord)
# =============================================================================

resource "google_cloud_run_v2_service" "subscriber" {
  name     = "crypto-subscriber"
  location = var.region
  project  = var.project_id

  template {
    service_account = google_service_account.subscriber_sa.email
    # Procesamos un mensaje de Pub/Sub por instancia a la vez para evitar picos de memoria/concurrencia.
    max_instance_request_concurrency = 1
    containers {
      image = var.subscriber_image
      #image = "gcr.io/cloudrun/hello"
      ports { container_port = 8080 }
      env {
        name  = "BQ_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.crypto_analytics.dataset_id
      }
      env {
        name  = "BQ_TABLE"
        value = google_bigquery_table.market_indicators.table_id
      }
      env {
        name  = "DISCORD_WEBHOOK_URL"
        value = var.discord_webhook_url
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true
      }
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
  }

  depends_on = [
    google_project_service.run,
  ]
}

# Permiso para que la SA del suscriptor invoque este servicio (push con OIDC)
resource "google_cloud_run_v2_service_iam_member" "subscriber_invoker" {
  project  = google_cloud_run_v2_service.subscriber.project
  location = google_cloud_run_v2_service.subscriber.location
  name     = google_cloud_run_v2_service.subscriber.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.subscriber_sa.email}"
}

# Pub/Sub usa su SA para entregar; Cloud Run acepta OIDC del subscriber_sa
# (el token OIDC en la push subscription es del subscriber_sa)
# Por tanto subscriber_sa debe ser invoker (arriba). Además Pub/Sub necesita
# generar el token: en push_config.oidc_token ponemos subscriber_sa, así que
# quien llama es la infra de Pub/Sub pero con identidad subscriber_sa. Correcto.
# También hay que permitir que la SA por defecto de Pub/Sub pueda invocar si no usas OIDC.
# Con OIDC, el request llega con token de subscriber_sa, así que solo ese invoker basta.
resource "google_cloud_run_v2_service_iam_member" "pubsub_sa_invoker" {
  project  = google_cloud_run_v2_service.subscriber.project
  location = google_cloud_run_v2_service.subscriber.location
  name     = google_cloud_run_v2_service.subscriber.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
