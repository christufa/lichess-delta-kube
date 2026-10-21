# Cluster-side glue that depends on Terraform outputs. It lives here rather than in
# deploy/ so that no deployment-specific names or IDs end up in the public repo:
# manifests in deploy/ refer to these objects by name only.

resource "kubernetes_namespace_v1" "lichess" {
  metadata {
    name = local.compute.jobs_namespace
    labels = {
      "app.kubernetes.io/part-of" = "lichess"
      # Pod Security Admission (#62): only pods meeting "restricted" may run here.
      "pod-security.kubernetes.io/enforce"         = "restricted"
      "pod-security.kubernetes.io/enforce-version" = "latest"
      "pod-security.kubernetes.io/warn"            = "restricted"
      "pod-security.kubernetes.io/audit"           = "restricted"
    }
  }
}

# Pods using this service account (plus the label azure.workload.identity/use=true)
# sign in to Azure as id-lichess-jobs: lake read/write, no secrets.
resource "kubernetes_service_account_v1" "jobs" {
  metadata {
    name      = local.compute.jobs_service_account
    namespace = kubernetes_namespace_v1.lichess.metadata[0].name
    annotations = {
      "azure.workload.identity/client-id" = local.compute.jobs_identity_client_id
    }
  }
}

# Names jobs need, injected with envFrom: configMapRef: lichess-config. Workflow
# parameters can read it too (valueFrom.configMapKeyRef), which Argo only allows for
# ConfigMaps with the configmap-type=Parameter label.
resource "kubernetes_config_map_v1" "lichess" {
  metadata {
    name      = "lichess-config"
    namespace = kubernetes_namespace_v1.lichess.metadata[0].name
    labels = {
      "workflows.argoproj.io/configmap-type" = "Parameter"
    }
  }
  data = {
    LICHESS_DEST     = local.platform.lichess_dest
    LAKE_ACCOUNT     = local.platform.lake_account_name
    LAKE_CONTAINER   = local.platform.lake_container_name
    ACR_LOGIN_SERVER = local.compute.acr_login_server
    # Iceberg REST catalog (deploy/apps/lakekeeper.yaml) and its warehouse
    LICHESS_CATALOG_URI = "http://lakekeeper.lakekeeper.svc.cluster.local:8181/catalog"
    LICHESS_WAREHOUSE   = "lichess"
    # Cost reporting (#31): spark/jobs/build_azure_cost.py queries these resource groups
    AZURE_SUBSCRIPTION_ID = data.azurerm_client_config.current.subscription_id
    COST_RESOURCE_GROUPS  = join(",", local.compute.cost_resource_groups)
  }
}

# Default artifact repository for workflows in this namespace: step outputs and
# archived container logs go to the lake under argo/…, written by the workflow pods
# themselves using workload identity (useSDKCreds → DefaultAzureCredential).
# https://argo-workflows.readthedocs.io/en/latest/configure-artifact-repository/#configuring-azure-blob-storage
resource "kubernetes_config_map_v1" "artifact_repositories" {
  metadata {
    name      = "artifact-repositories"
    namespace = kubernetes_namespace_v1.lichess.metadata[0].name
    annotations = {
      "workflows.argoproj.io/default-artifact-repository" = "lake"
    }
  }
  data = {
    lake = yamlencode({
      archiveLogs = true
      azure = {
        endpoint       = "https://${local.platform.lake_account_name}.blob.core.windows.net"
        container      = local.platform.lake_container_name
        blobNameFormat = "argo/{{workflow.creationTimestamp.Y}}/{{workflow.creationTimestamp.m}}/{{workflow.name}}/{{pod.name}}"
        useSDKCreds    = true
      }
    })
  }
}
