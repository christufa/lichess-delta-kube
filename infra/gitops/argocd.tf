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
    # Single-user setup reached through `kubectl port-forward`: no Dex (SSO comes
    # with #45) and no notifications controller.
    dex           = { enabled = false }
    notifications = { enabled = false }
    # Small requests: everything shares one D4as_v5 with the cluster add-ons.
    controller     = { resources = { requests = { cpu = "250m", memory = "512Mi" } } }
    repoServer     = { resources = { requests = { cpu = "100m", memory = "256Mi" } } }
    server         = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
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
