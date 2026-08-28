#!/usr/bin/env bash
# Produce every failure in src/quayz/failures.py against a real cluster, and record what each
# instrument said about it.
#
# Usage:  scripts/measure_failures.sh [cluster-name]
#
# It creates the cluster and destroys it, so it leaves nothing behind and needs nothing set up.
# A cold create is about a hundred seconds on a machine that has never pulled the node image and
# about twenty-five afterwards, which is cheap enough to do per run rather than maintaining a
# long-lived cluster and inheriting its dirty state.
#
# WHY THE TRANSCRIPTS ARE NOT BYTE COMPARED. They carry pod names with random suffixes, IP
# addresses, ages and restart counts that depend on how long a step took. summary.json carries
# the decisive facts alone, and that is what CI diffs. The transcripts are for a reader.
set -euo pipefail

CLUSTER="${1:-quayz-measure}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/evidence/cluster"
CHART="$ROOT/charts/deploy-canary"
SELECTOR="app.kubernetes.io/name=deploy-canary"

for tool in kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 1; }
done
mkdir -p "$OUT"

cleanup() { kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> creating cluster $CLUSTER"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --wait 180s >/dev/null
CONTEXT="kind-$CLUSTER"
k() { kubectl --context "$CONTEXT" "$@"; }
h() { helm --kube-context "$CONTEXT" "$@"; }

# The one field that separates a crash loop from an OOMKill, read as data rather than parsed
# out of a table. `kubectl get pods` shows the same thing for both.
terminated() {
  k get pods -l "$SELECTOR" -o json |
    python3 -c '
import json, sys
pods = json.load(sys.stdin)["items"]
for pod in pods:
    for status in pod["status"].get("containerStatuses", []):
        last = status.get("lastState", {}).get("terminated", {})
        print(status["restartCount"], last.get("reason", "none"), last.get("exitCode", "none"))
        raise SystemExit
print("0 none none")
'
}

# Readiness read from the EndpointSlice conditions, NEVER from the wide output. Measured on
# 28-8-2026: with one pod not ready, `kubectl get endpointslice -o wide` listed all three IP
# addresses in its ENDPOINTS column, including the one whose conditions.ready was false. A test
# counting that column calls a broken Service healthy.
readiness() {
  k get endpointslice -l "kubernetes.io/service-name=canary-canary" -o json |
    python3 -c '
import json, sys
slices = json.load(sys.stdin)["items"]
ready = notready = 0
for entry in slices:
    for endpoint in entry.get("endpoints", []):
        if endpoint["conditions"].get("ready"):
            ready += 1
        else:
            notready += 1
print(f"{ready} {notready}")
'
}

reset() { h uninstall canary >/dev/null 2>&1 || true; sleep 3; }

record() {
  local name="$1" file="$2"
  {
    echo "$ helm install canary charts/deploy-canary${3:+ $3}"
    echo "# failure: $name"
    echo
    echo "--- kubectl get pods ---"
    k get pods -l "$SELECTOR" --no-headers || true
    echo
    echo "--- the field that separates them ---"
    echo "restartCount lastState.terminated.reason exitCode"
    terminated
    echo
    echo "--- kubectl logs, in full ---"
    k logs -l "$SELECTOR" --tail=20 2>&1 || true
    echo
    echo "--- EndpointSlice conditions ---"
    echo "ready notready"
    readiness
    echo
    echo "--- and the WIDE output of the same EndpointSlice, which must not be trusted ---"
    k get endpointslice -l "kubernetes.io/service-name=canary-canary" -o wide --no-headers 2>&1 || true
  } > "$OUT/$file"
}

echo "==> healthy"
reset
h install canary "$CHART" --wait --timeout 120s >/dev/null
HEALTHY_READY=$(readiness)
record "none, this is the control" "healthy.txt" ""

echo "==> alive but never ready"
reset
h install canary "$CHART" --set failure.neverReady=true --timeout 45s >/dev/null 2>&1 || true
sleep 20
NEVERREADY_TERM=$(terminated)
NEVERREADY_READY=$(readiness)
NEVERREADY_WIDE=$(k get endpointslice -l "kubernetes.io/service-name=canary-canary" -o wide --no-headers 2>/dev/null | awk '{print $4}' | tr ',' '\n' | grep -c . || echo 0)
record "alive but never ready" "alive-but-never-ready.txt" "--set failure.neverReady=true"

echo "==> crash loop"
reset
h install canary "$CHART" --set failure.crashLoop=true --set replicaCount=1 --timeout 45s >/dev/null 2>&1 || true
sleep 40
CRASH_TERM=$(terminated)
CRASH_LOGS=$(k logs -l "$SELECTOR" --tail=20 2>/dev/null | wc -l | tr -d ' ')
record "crash loop" "crash-loop.txt" "--set failure.crashLoop=true"

echo "==> killed for memory"
reset
h install canary "$CHART" --set failure.outOfMemory=true --set replicaCount=1 --timeout 45s >/dev/null 2>&1 || true
sleep 45
OOM_TERM=$(terminated)
OOM_LOGS=$(k logs -l "$SELECTOR" --tail=20 2>/dev/null | grep -ci 'error\|fail\|kill' || true)
record "killed for memory" "killed-for-memory.txt" "--set failure.outOfMemory=true"

# The decisive facts, and nothing that varies between runs. This is what CI diffs.
cat > "$OUT/summary.json" <<JSON
{
  "healthy_endpoints_ready": $(echo "$HEALTHY_READY" | awk '{print $1}'),
  "healthy_endpoints_not_ready": $(echo "$HEALTHY_READY" | awk '{print $2}'),

  "never_ready_restart_count": $(echo "$NEVERREADY_TERM" | awk '{print $1}'),
  "never_ready_endpoints_ready": $(echo "$NEVERREADY_READY" | awk '{print $1}'),
  "never_ready_endpoints_not_ready": $(echo "$NEVERREADY_READY" | awk '{print $2}'),
  "never_ready_addresses_in_the_wide_column": $NEVERREADY_WIDE,

  "crash_loop_reason": "$(echo "$CRASH_TERM" | awk '{print $2}')",
  "crash_loop_exit_code": $(echo "$CRASH_TERM" | awk '{print $3}'),
  "crash_loop_log_lines": $CRASH_LOGS,

  "oom_reason": "$(echo "$OOM_TERM" | awk '{print $2}')",
  "oom_exit_code": $(echo "$OOM_TERM" | awk '{print $3}'),
  "oom_log_lines_mentioning_a_problem": ${OOM_LOGS:-0}
}
JSON

echo
echo "==> written to docs/evidence/cluster:"
ls -1 "$OUT"
echo
cat "$OUT/summary.json"
