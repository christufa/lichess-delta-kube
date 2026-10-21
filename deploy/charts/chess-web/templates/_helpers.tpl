{{/* /config.json for the dashboard: the chat's Entra tenant and app (web/src/chat/config.ts). */}}
{{- define "chess-web.config" -}}
{{- if and .Values.chat.tenantId .Values.chat.clientId -}}
{{- dict "tenantId" .Values.chat.tenantId "chatClientId" .Values.chat.clientId | toJson -}}
{{- end -}}
{{- end -}}
