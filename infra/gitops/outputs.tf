output "argocd_ui" {
  value = "kubectl port-forward -n argocd svc/argocd-server 8080:443   →   https://localhost:8080 (user: admin)"
}

output "argocd_initial_password" {
  value = "kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}'  (base64; change it in the UI, then delete the secret)"
}

output "jupyter_token" {
  description = "JupyterLab token: http://localhost:8888/lab?token=<this> after kubectl port-forward -n lichess svc/jupyter 8888 4040"
  value       = kubernetes_secret_v1.jupyter.data.token
  sensitive   = true
}
