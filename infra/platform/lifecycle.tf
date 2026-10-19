# Landed Parquet (written by lichess-ingest under <prefix>/raw/games/) is only the input to
# lake.raw.games: once a month is published, the Iceberg table holds its own copy. Delete
# landed files after staging_retention_days, which leaves time to re-publish or investigate
# a rejected month. Iceberg data (Lakekeeper locations), manifests and _state are untouched.
# https://learn.microsoft.com/azure/storage/blobs/lifecycle-management-overview
resource "azurerm_storage_management_policy" "lake" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "delete-landed-parquet"
    enabled = true
    filters {
      prefix_match = ["${azurerm_storage_container.lake.name}/bootstrap/raw/games/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.staging_retention_days
      }
    }
  }
}
