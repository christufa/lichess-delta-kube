# Argo CD, plus one root Application ("app-of-apps") that syncs deploy/apps from git.
# Everything after this is managed by Argo CD from the repository.

resource "kubernetes_namespace_v1" "argocd" {
  metadata {
    name = "argocd"
  }
}

# https://github.com/argoproj/argo-helm/tree/main/charts/argo-cd
resource "helm_release" "argocd" {
  name       = "argocd"
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.argocd_chart_version
  timeout    = 900

  values = [yamlencode({
    # Sign-in with Microsoft Entra ID directly (no Dex), using workload identity: the
    # argocd-server pod proves itself to Entra with its Kubernetes token, so there is no
    # client secret anywhere. Reached at https://chess-cd.generops.dev behind Cloudflare
    # Access, or with kubectl port-forward.
    dex           = { enabled = false }
    notifications = { enabled = false }
    configs = {
      cm = {
        url             = "https://${local.private_hosts.cd.hostname}"
        "admin.enabled" = tostring(var.argocd_admin_enabled)
        "oidc.config" = yamlencode({
          name     = "Microsoft"
          issuer   = "https://login.microsoftonline.com/${data.azuread_client_config.current.tenant_id}/v2.0"
          clientID = azuread_application.argocd.client_id
          azure    = { useWorkloadIdentity = true }
          requestedIDTokenClaims = {
            groups = { essential = true }
          }
          requestedScopes = ["openid", "profile", "email"]
        })
      }
      # Members of the Entra group "<prefix> admins" are admins; anyone else who somehow
      # signs in gets nothing (empty default role).
      rbac = {
        "policy.default" = ""
        "policy.csv"     = "g, ${azuread_group.admins.object_id}, role:admin\n"
        scopes           = "[groups]"
      }
    }
    # Small requests: everything shares the system pool with the cluster add-ons.
    controller = { resources = { requests = { cpu = "250m", memory = "512Mi" } } }
    repoServer = { resources = { requests = { cpu = "100m", memory = "256Mi" } } }
    server = {
      resources = { requests = { cpu = "50m", memory = "128Mi" } }
      podLabels = { "azure.workload.identity/use" = "true" }
      serviceAccount = {
        annotations = { "azure.workload.identity/client-id" = azuread_application.argocd.client_id }
      }
    }
    redis          = { resources = { requests = { cpu = "50m", memory = "64Mi" } } }
    applicationSet = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
  })]
}

# https://github.com/argoproj/argo-helm/tree/main/charts/argocd-apps
resource "helm_release" "root_app" {
  name       = "argocd-root"
  namespace  = kubernetes_namespace_v1.argocd.metadata[0].name
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = var.argocd_apps_chart_version

  values = [yamlencode({
    applications = {
      root = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.repo_url
          targetRevision = var.repo_revision
          path           = "deploy/apps"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd] # needs the Application CRD
}
