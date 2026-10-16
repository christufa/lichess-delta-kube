# One small always-on node for cluster add-ons; all real work goes to a spot pool
# that scales from 0. Idle cost is the system node, its disk and the load balancer.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/kubernetes_cluster
resource "azurerm_kubernetes_cluster" "main" {
  name                = "aks-${var.project}"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  dns_prefix          = "aks-${var.project}"
  node_resource_group = "rg-${var.project}-compute-nodes" # AKS creates and owns this one
  sku_tier            = "Free"                            # no uptime SLA; control plane is free

  automatic_upgrade_channel = "patch"     # stay on the latest patch of the minor version
  node_os_upgrade_channel   = "NodeImage" # weekly node image updates

  # Workload identity: pods exchange Kubernetes tokens for Azure tokens (identity.tf).
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  # kubectl via Entra ID + Azure RBAC only. The API server's authorized IP ranges
  # are set by hand in Azure (see infra/README.md); until then it answers any IP.
  local_account_disabled            = true
  role_based_access_control_enabled = true
  azure_active_directory_role_based_access_control {
    tenant_id          = data.azurerm_client_config.current.tenant_id
    azure_rbac_enabled = true
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.system_vm_size
    node_count                   = 1
    only_critical_addons_enabled = true # taint: nothing but cluster add-ons runs here
    os_disk_size_gb              = 64
    vnet_subnet_id               = local.platform.aks_subnet_id
    temporary_name_for_rotation  = "systemtmp" # lets Terraform change the VM size without a rebuild

    upgrade_settings {
      max_surge = "10%"
    }
  }

  # Azure CNI Overlay + Cilium: pods get IPs from pod_cidr, not the subnet.
  # None of these ranges may overlap the VNet (10.10.0.0/16).
  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    pod_cidr            = "10.244.0.0/16"
    service_cidr        = "10.0.0.0/16"
    dns_service_ip      = "10.0.0.10"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
  }

  # Remove idle batch nodes quickly; they cost money every minute they exist.
  auto_scaler_profile {
    scale_down_unneeded        = "5m"
    scale_down_delay_after_add = "5m"
  }

  tags = local.tags

  lifecycle {
    ignore_changes = [api_server_access_profile] # authorized IP ranges are set by hand in Azure
  }

  depends_on = [azurerm_role_assignment.aks_network]
}

# Spot batch pool: up to ~80-90% cheaper than on-demand, can be evicted at any time.
# Fine for ingest (a month is re-run from scratch) and, later, Spark with Celeborn.
# Pods need a toleration for the spot taint and a nodeSelector workload=batch.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/kubernetes_cluster_node_pool
resource "azurerm_kubernetes_cluster_node_pool" "batch" {
  name                  = "batch"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = var.batch_vm_size
  vnet_subnet_id        = local.platform.aks_subnet_id
  os_disk_size_gb       = var.batch_os_disk_gb

  priority        = "Spot"
  eviction_policy = "Delete"
  spot_max_price  = -1 # pay up to the on-demand price; never evicted for price

  auto_scaling_enabled = true
  min_count            = 0
  max_count            = var.batch_max_nodes
  node_count           = 0

  node_labels = { workload = "batch" }
  node_taints = ["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"] # AKS adds this anyway

  tags = local.tags

  lifecycle {
    ignore_changes = [
      node_count, # the autoscaler owns this
      # AKS adds this label to every spot pool itself; without this, every plan
      # would try to remove it.
      node_labels["kubernetes.azure.com/scalesetpriority"],
    ]
  }
}
