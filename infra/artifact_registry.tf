# =============================================================================
# Artifact Registry — repo Docker y política de limpieza (Free Tier: 2 imágenes)
# Solo se conservan job y subscriber; de cada una solo la última versión.
# =============================================================================

resource "google_artifact_registry_repository" "crypto_monitor" {
  project       = var.project_id
  location      = var.region
  repository_id = "crypto-monitor"
  description   = "Imágenes Docker del crypto monitor (job + subscriber)"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-latest-per-image"
    action = "KEEP"

    most_recent_versions {
      keep_count = 1
    }
  }

  cleanup_policy_dry_run = false

  depends_on = [google_project_service.artifact_registry]
}
