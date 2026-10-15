output "resource_group_name" {
  value = azurerm_resource_group.platform.name
}

output "lake_account_name" {
  value = azurerm_storage_account.lake.name
}

output "lake_container_name" {
  value = azurerm_storage_container.lake.name
}

# Paste into ingest/.env as LICHESS_DEST
output "lichess_dest" {
  value = "abfss://${azurerm_storage_container.lake.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/bootstrap"
}

# ---- Read by ../compute through terraform_remote_state ----

output "location" {
  value = azurerm_resource_group.platform.location
}

output "lake_account_id" {
  value = azurerm_storage_account.lake.id
}

output "aks_subnet_id" {
  value = azurerm_subnet.aks.id
}
