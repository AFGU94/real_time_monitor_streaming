# =============================================================================
# Pub/Sub - Corazón del streaming en GCP
# =============================================================================

resource "google_pubsub_topic" "crypto_prices" {
  name = "crypto-prices"
}

# Suscripción push: envía cada mensaje al endpoint de Cloud Run
resource "google_pubsub_subscription" "crypto_prices_sub" {
  name  = "crypto-prices-sub"
  topic = google_pubsub_topic.crypto_prices.name

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.subscriber.uri}/"
    oidc_token {
      service_account_email = google_service_account.subscriber_sa.email
    }
    attributes = {}
  }

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  ack_deadline_seconds = 60
}
