# --- Monitoring (#31): Azure Managed Prometheus + alerts ---------------------------------
# No Prometheus server to run: the AKS metrics add-on (ama-metrics, kube-system) scrapes
# kubelet/cAdvisor/kube-state-metrics/node-exporter plus our PodMonitors
# (deploy/monitoring) and writes to an Azure Monitor workspace. Grafana (deploy/apps/
# grafana.yaml) reads it with workload identity; alert rules below run in Azure and email
# you through an action group.
# https://learn.microsoft.com/azure/azure-monitor/containers/kubernetes-monitoring-enable
resource "azurerm_monitor_workspace" "main" {
  name                = "amw-${var.project}"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

locals {
  prom_name = "MSProm-${azurerm_resource_group.compute.location}-${azurerm_kubernetes_cluster.main.name}"
}

resource "azurerm_monitor_data_collection_endpoint" "prometheus" {
  name                = substr(local.prom_name, 0, 44)
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  kind                = "Linux"
  tags                = local.tags
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  name                        = local.prom_name
  resource_group_name         = azurerm_resource_group.compute.name
  location                    = azurerm_resource_group.compute.location
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.prometheus.id
  kind                        = "Linux"
  tags                        = local.tags

  destinations {
    monitor_account {
      monitor_account_id = azurerm_monitor_workspace.main.id
      name               = "MonitoringAccount1"
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["MonitoringAccount1"]
  }

  data_sources {
    prometheus_forwarder {
      streams = ["Microsoft-PrometheusMetrics"]
      name    = "PrometheusDataSource"
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "prometheus" {
  name                    = "MSProm-${azurerm_kubernetes_cluster.main.name}"
  target_resource_id      = azurerm_kubernetes_cluster.main.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.prometheus.id
}

resource "azurerm_monitor_data_collection_rule_association" "prometheus_endpoint" {
  name                        = "configurationAccessEndpoint" # fixed name required by Azure
  target_resource_id          = azurerm_kubernetes_cluster.main.id
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.prometheus.id
}

# --- Grafana's identity: read the workspace, nothing else -------------------------------
resource "azurerm_user_assigned_identity" "grafana" {
  name                = "id-${var.project}-grafana"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "grafana_metrics" {
  scope                = azurerm_monitor_workspace.main.id
  role_definition_name = "Monitoring Data Reader"
  principal_id         = azurerm_user_assigned_identity.grafana.principal_id
}

resource "azurerm_federated_identity_credential" "grafana" {
  name                      = "aks-monitoring-grafana"
  user_assigned_identity_id = azurerm_user_assigned_identity.grafana.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:monitoring:grafana"
}

# --- Alerts -------------------------------------------------------------------------------
resource "azurerm_monitor_action_group" "owner" {
  name                = "ag-${var.project}-owner"
  resource_group_name = azurerm_resource_group.compute.name
  short_name          = "lichess"
  tags                = local.tags

  # Azure only emails a receiver whose address has been verified (a code it emails once; until
  # then the action group "runs" but sends nothing, and its test API says "There are no valid
  # receivers"). The first address never got the verification mail, so var.alert_email is a
  # +alias of the same mailbox, verified on 2026-10-21. Changing the address means verifying
  # again. Alerts come from azure-noreply@microsoft.com, azureemail-noreply@microsoft.com and
  # alerts-noreply@mail.windowsazure.com; allow all three in the mailbox.
  email_receiver {
    name                    = "owner-email"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

locals {
  # name => [PromQL, for (ISO 8601), severity 0-4 (0 = critical), summary]
  alerts = {
    # Azure Managed Prometheus allows only `=` on the metric name; Argo's phases are lowercase and
    # its `namespace` label is the controller's. The counter catches each failure; the gauge of
    # failed workflows catches one right after a controller restart (a new series' first sample
    # isn't an increase).
    WorkflowFailed = [
      "(sum(increase(argo_workflows_total_count{phase=~\"failed|error\"}[15m])) > 0) or (sum(delta(argo_workflows_gauge{phase=~\"failed|error\"}[15m])) > 0)",
      "PT1M", 2, "An Argo workflow failed (see Argo Workflows at chess-flows)",
    ]
    PodCrashLooping = [
      "max by (namespace, pod) (max_over_time(kube_pod_container_status_waiting_reason{reason=\"CrashLoopBackOff\", namespace!=\"kube-system\"}[5m])) >= 1",
      "PT10M", 2, "{{ $labels.namespace }}/{{ $labels.pod }} is crash-looping",
    ]
    PodStuckPending = [
      "sum by (namespace, pod) (kube_pod_status_phase{phase=\"Pending\", namespace!=\"kube-system\"}) > 0",
      "PT30M", 3, "{{ $labels.namespace }}/{{ $labels.pod }} has been Pending for 30 minutes (spot capacity or quota?)",
    ]
    NodeNotReady = [
      "kube_node_status_condition{condition=\"Ready\", status=\"true\"} == 0",
      "PT10M", 2, "Node {{ $labels.node }} is not Ready",
    ]
    TunnelDown = [
      "sum(cloudflared_tunnel_ha_connections) < 1 or absent(cloudflared_tunnel_ha_connections)",
      "PT5M", 1, "cloudflared has no connection to Cloudflare: the chess-* sites are down",
    ]
    ArgoAppUnhealthy = [
      "max by (name) (argocd_app_info{health_status!~\"Healthy|Progressing\"}) == 1",
      "PT15M", 3, "Argo CD app {{ $labels.name }} is not healthy",
    ]
    VolumeAlmostFull = [
      "kubelet_volume_stats_available_bytes / kubelet_volume_stats_capacity_bytes < 0.1",
      "PT15M", 3, "Volume {{ $labels.persistentvolumeclaim }} in {{ $labels.namespace }} is over 90% full",
    ]
  }
}

resource "azurerm_monitor_alert_prometheus_rule_group" "platform" {
  name                = "${var.project}-platform"
  resource_group_name = azurerm_resource_group.compute.name
  location            = azurerm_resource_group.compute.location
  cluster_name        = azurerm_kubernetes_cluster.main.name
  description         = "Workflows, pods, nodes, the Cloudflare tunnel and Argo CD apps"
  rule_group_enabled  = true
  interval            = "PT1M"
  scopes              = [azurerm_monitor_workspace.main.id, azurerm_kubernetes_cluster.main.id]
  tags                = local.tags

  dynamic "rule" {
    for_each = local.alerts
    content {
      alert      = rule.key
      enabled    = true
      expression = rule.value[0]
      for        = rule.value[1]
      severity   = rule.value[2]
      annotations = {
        summary = rule.value[3]
      }
      action {
        action_group_id = azurerm_monitor_action_group.owner.id
      }
      alert_resolution {
        auto_resolved   = true
        time_to_resolve = "PT10M"
      }
    }
  }
}
