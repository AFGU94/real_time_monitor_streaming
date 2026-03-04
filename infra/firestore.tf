# =============================================================================
# Firestore - TTL en crypto_alerts para mantener Free Tier
# Los documentos deben incluir el campo expireAt (timestamp) en el código Python.
# Firestore borra automáticamente documentos cuando expireAt ha pasado.
# =============================================================================

resource "google_firestore_field" "crypto_alerts_expire_ttl" {
  project    = var.project_id
  database   = "(default)"
  collection = "crypto_alerts"
  field      = "expireAt"

  ttl_config {} # Borrado automático cuando expireAt < ahora
}
