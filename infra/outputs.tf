output "project_id" {
  value = var.project_id
}

output "pubsub_topic" {
  value = google_pubsub_topic.crypto_prices.name
}

output "bigquery_dataset" {
  value = google_bigquery_dataset.crypto_analytics.dataset_id
}

output "bigquery_table" {
  value = google_bigquery_table.market_indicators.table_id
}

output "subscriber_url" {
  value = google_cloud_run_v2_service.subscriber.uri
}

output "job_name" {
  value = google_cloud_run_v2_job.crypto_ingestion.name
}
