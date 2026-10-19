# Deployment-specific facts (registry, Key Vault, tenant, …) handed to GitOps without
# putting them in the public repo.
#
# Argo CD reads cluster settings from Secrets labelled argocd.argoproj.io/secret-type=cluster.
# This one describes the in-cluster destination and carries the facts as annotations.
# The `platform` ApplicationSet (deploy/apps/platform.yaml) selects it with the cluster
# generator and passes the annotations to deploy/charts/platform as Helm values.
# https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Generators-Cluster/
data "azurerm_client_config" "current" {}

locals {
  facts_prefix = "lichess.generops.dev"
  cluster_facts = {
    "acr-login-server"    = local.compute.acr_login_server
    "key-vault-url"       = local.compute.key_vault_uri
    "tenant-id"           = data.azurerm_client_config.current.tenant_id
    "eso-namespace"       = local.compute.eso_namespace
    "eso-service-account" = local.compute.eso_service_account
    "kyverno-client-id"   = local.compute.kyverno_identity_client_id
    "grafana-client-id"   = local.compute.grafana_identity_client_id
    "prometheus-url"      = local.compute.prometheus_query_endpoint
    "grafana-url"         = "https://${local.private_hosts.grafana.hostname}"
  }
}

resource "kubernetes_secret_v1" "in_cluster" {
  metadata {
    name      = "in-cluster"
    namespace = kubernetes_namespace_v1.argocd.metadata[0].name
    labels = {
      "argocd.argoproj.io/secret-type" = "cluster"
      "${local.facts_prefix}/facts"    = "true"
    }
    annotations = { for k, v in local.cluster_facts : "${local.facts_prefix}/${k}" => v }
  }
  data = {
    name   = "in-cluster"
    server = "https://kubernetes.default.svc"
    config = jsonencode({ tlsClientConfig = { insecure = false } })
  }
}

# --- External Secrets Operator: namespace + the identity it uses for Key Vault ---------
# The operator itself (Helm chart via Argo CD) runs with its own service account; the
# ClusterSecretStore signs in to Key Vault as this one (workload identity, no secrets).
resource "kubernetes_namespace_v1" "eso" {
  metadata {
    name   = local.compute.eso_namespace
    labels = { "app.kubernetes.io/part-of" = "lichess" }
  }
}

resource "kubernetes_service_account_v1" "eso_azure_kv" {
  metadata {
    name      = local.compute.eso_service_account
    namespace = kubernetes_namespace_v1.eso.metadata[0].name
    annotations = {
      "azure.workload.identity/client-id" = local.compute.eso_identity_client_id
      "azure.workload.identity/tenant-id" = data.azurerm_client_config.current.tenant_id
    }
  }
}
