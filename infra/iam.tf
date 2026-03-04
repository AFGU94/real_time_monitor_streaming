# =============================================================================
# Service Accounts e IAM
# =============================================================================

# SA para el suscriptor Cloud Run (recibe push de Pub/Sub, escribe BigQuery/Firestore)
resource "google_service_account" "subscriber_sa" {
  account_id   = "crypto-subscriber"
  display_name = "Crypto Pub/Sub Subscriber"
  project      = var.project_id
}

resource "google_project_iam_member" "subscriber_bq" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.subscriber_sa.email}"
}

resource "google_project_iam_member" "subscriber_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.subscriber_sa.email}"
}

# El permiso para que Pub/Sub invoque el subscriber está en cloudrun.tf:
# google_cloud_run_v2_service_iam_member.pubsub_sa_invoker (a nivel del servicio).
# No hace falta run.invoker a nivel de proyecto para la SA de Pub/Sub.

# SA para el Job (lee BQ, publica Pub/Sub)
resource "google_service_account" "job_sa" {
  account_id   = "crypto-job"
  display_name = "Crypto Ingestion Job"
  project      = var.project_id
}

# dataViewer: leer datos de tablas. jobUser: crear/ejecutar jobs de consulta (SELECT crea un job en BQ).
resource "google_project_iam_member" "job_bq_read" {
  project = var.project_id
  role    = "roles/bigquery.dataViewer"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

resource "google_project_iam_member" "job_bq_jobuser" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

resource "google_project_iam_member" "job_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

# Scheduler usa la SA por defecto o una dedicada para ejecutar el Job
resource "google_project_iam_member" "scheduler_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}
