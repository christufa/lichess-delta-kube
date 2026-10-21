"""Generates lichess-workflows.json, the Grafana dashboard for Argo Workflows (#31).

  python deploy/grafana-dashboards/lichess_workflows.py   # then commit the JSON

Queries use only what Azure Managed Prometheus allows (no regex on metric names) and the
Argo Workflows controller's metrics (argo_workflows_*), whose phases are lowercase."""
import json
import os
import sys

DS = {"type": "prometheus", "uid": "amp"}
T = "argo_workflows_workflowtemplate"
SEL = 'name=~"$template"'
panels = []
y = 0


def row(title):
    global y
    panels.append({"type": "row", "title": title, "collapsed": False, "id": len(panels) + 1,
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
    y += 1


def add(p, w, h, x):
    p["datasource"] = DS
    p["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
    p["id"] = len(panels) + 1
    panels.append(p)


def target(expr, legend="", instant=False, fmt="time_series", ref="A"):
    t = {"datasource": DS, "expr": expr, "refId": ref, "legendFormat": legend or "__auto", "format": fmt}
    if instant:
        t.update({"instant": True, "range": False})
    return t


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def stat(title, expr, desc, x, thresholds):
    add({"type": "stat", "title": title, "description": desc,
         "targets": [target(expr, instant=True)],
         "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                     "graphMode": "none", "textMode": "value"},
         "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "noValue": "0",
                                      "color": {"mode": "thresholds"}, "thresholds": thresholds},
                         "overrides": []}}, 4, 4, x)


def color(name, c):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


def bars(title, expr, legend, desc, w, x, overrides=()):
    add({"type": "timeseries", "title": title, "description": desc, "interval": "1h",
         "targets": [target(expr, legend)],
         "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "desc"}},
         "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "min": 0,
                                      "custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1,
                                                 "stacking": {"mode": "normal"}}},
                         "overrides": list(overrides)}}, w, 8, x)


row("Now")
stat("Running", 'sum(argo_workflows_gauge{phase="running"})',
     "Workflows running right now.", 0, steps(("blue", None)))
stat("Succeeded (24h)", f'sum(round(increase({T}_triggered_total{{phase="succeeded", {SEL}}}[24h])))',
     "Runs that succeeded in the last 24 hours.", 4, steps(("green", None)))
stat("Failed (24h)", f'sum(round(increase({T}_triggered_total{{phase=~"failed|error", {SEL}}}[24h])))',
     "Runs that failed or errored in the last 24 hours. Each one also fires the WorkflowFailed alert.",
     8, steps(("green", None), ("red", 1)))
stat("Pods running", 'sum(argo_workflows_pods_gauge{phase="running"})',
     "Pods the workflow controller runs (Spark's own driver and executor pods aren't counted).",
     12, steps(("blue", None)))
stat("Pods pending", 'sum(argo_workflows_pods_gauge{phase="pending"})',
     "Workflow pods waiting for a node (spot capacity, quota).", 16, steps(("green", None), ("orange", 1)))
stat("Controller queue", "sum(argo_workflows_queue_depth_gauge)",
     "Items waiting in the workflow controller's queues. Above 0 for long means it's falling behind.",
     20, steps(("green", None), ("orange", 10)))
y += 4

row("Runs")
bars("Runs finished, by outcome",
     f'sum by (phase) (round(increase({T}_triggered_total{{phase=~"succeeded|failed|error", {SEL}}}[$__interval])))',
     "{{phase}}", "Workflow runs that ended in each interval.", 12, 0,
     [color("succeeded", "green"), color("failed", "red"), color("error", "orange")])
bars("Runs started, by template",
     f'sum by (name) (round(increase({T}_triggered_total{{phase="new", {SEL}}}[$__interval])))',
     "{{name}}", "Runs started from each WorkflowTemplate. spark-job covers every Spark build and maintenance run.",
     12, 12)
y += 8

row("Run time")
R = "[$__range]"
add({"type": "table", "title": "Per template (selected time range)",
     "description": "Runs, failures and run time per WorkflowTemplate over the dashboard's time range. "
                    "p90 comes from the controller's histogram, whose buckets stop at 7500 s: "
                    "longer runs (backfills) count as over 7500 s.",
     "targets": [
         target(f"sum by (name) (round(increase({T}_runtime_count{{{SEL}}}{R})))", instant=True, fmt="table", ref="A"),
         target(f'sum by (name) (round(increase({T}_triggered_total{{phase=~"failed|error", {SEL}}}{R})))',
                instant=True, fmt="table", ref="B"),
         target(f"sum by (name) (increase({T}_runtime_sum{{{SEL}}}{R})) / sum by (name) (increase({T}_runtime_count{{{SEL}}}{R}))",
                instant=True, fmt="table", ref="C"),
         target(f"histogram_quantile(0.9, sum by (le, name) (increase({T}_runtime_bucket{{{SEL}}}{R})))",
                instant=True, fmt="table", ref="D"),
     ],
     "transformations": [
         {"id": "merge", "options": {}},
         {"id": "organize", "options": {
             "excludeByName": {"Time": True},
             "renameByName": {"name": "Template", "Value #A": "Runs", "Value #B": "Failed",
                              "Value #C": "Average run time", "Value #D": "p90 run time"}}},
         {"id": "sortBy", "options": {"sort": [{"field": "Runs", "desc": True}]}},
     ],
     "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
         {"matcher": {"id": "byRegexp", "options": "Average run time|p90 run time"},
          "properties": [{"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 0}]},
         {"matcher": {"id": "byName", "options": "Failed"},
          "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                         {"id": "thresholds", "value": steps(("text", None), ("red", 1))}]},
     ]},
     "options": {"showHeader": True}}, 12, 9, 0)
add({"type": "timeseries", "title": "Average run time, by template", "interval": "1h",
     "description": "Average run time of the runs that finished in each interval.",
     "targets": [target(f"sum by (name) (increase({T}_runtime_sum{{{SEL}}}[$__interval])) / "
                        f"sum by (name) (increase({T}_runtime_count{{{SEL}}}[$__interval]))", "{{name}}")],
     "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi", "sort": "desc"}},
     "fieldConfig": {"defaults": {"unit": "s", "min": 0,
                                  "custom": {"drawStyle": "points", "pointSize": 8, "spanNulls": False}},
                     "overrides": []}}, 12, 9, 12)
y += 9

row("Pods")
add({"type": "timeseries", "title": "Workflow pods, by phase",
     "description": "Pods the workflow controller runs (not Spark's own driver and executor pods).",
     "targets": [target("sum by (phase) (argo_workflows_pods_gauge)", "{{phase}}")],
     "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
     "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "min": 0,
                                  "custom": {"drawStyle": "line", "fillOpacity": 20, "stacking": {"mode": "normal"}}},
                     "overrides": [color("running", "blue"), color("pending", "orange")]}}, 24, 7, 0)

dash = {
    "uid": "lichess-workflows",
    "title": "Lichess workflows",
    "tags": ["lichess", "argo-workflows"],
    "description": "Argo Workflows runs, outcomes and run time per WorkflowTemplate (#31). "
                   "Individual runs are in the Argo Workflows UI (link at the top).",
    "timezone": "utc",
    "editable": False,
    "graphTooltip": 1,
    "refresh": "1m",
    "schemaVersion": 41,
    "time": {"from": "now-7d", "to": "now"},
    "links": [{"title": "Argo Workflows (individual runs)", "type": "link", "icon": "external link",
               "url": "https://chess-flows.generops.dev/workflows/lichess", "targetBlank": True}],
    "templating": {"list": [{
        "name": "template", "label": "WorkflowTemplate", "type": "query", "datasource": DS,
        "query": {"query": f"label_values({T}_triggered_total, name)", "refId": "template"},
        "definition": f"label_values({T}_triggered_total, name)",
        "multi": True, "includeAll": True, "allValue": ".*",
        "current": {"text": "All", "value": "$__all"}, "refresh": 2, "sort": 1}]},
    "panels": panels,
}
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "lichess-workflows.json")
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(dash, fh, indent=2)
    fh.write("\n")
print(len(panels), "panels")
