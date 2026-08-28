#!/usr/bin/env bash
# What happens when a deploy fails: unattended, and by hand.
#
# Usage:  scripts/measure_rollback.sh [cluster-name]
#
# Two separate claims, measured separately, because they are answers to different questions.
#
#   --atomic       the pipeline's answer. A failed upgrade puts the previous release back with
#                  nobody watching, and the command exits non-zero so the build goes red.
#   helm rollback  the operator's answer, at three in the morning, when the deploy went out
#                  hours ago and --atomic is long past.
#
# The revision history is the evidence for both. A rollback that leaves no trace is
# indistinguishable from never having deployed, and "what is actually running" is the question
# somebody has in the middle of an incident.
set -euo pipefail

CLUSTER="${1:-quayz-rollback}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/evidence/rollback"
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
# THE NODE IMAGE, WHICH THE WORKFLOW PINS BY DIGEST AND WHICH NEVER REACHED A CLUSTER. The CI
# job passed node_image to helm/kind-action alongside install_only: true, and install_only means
# the action installs the binaries and creates nothing, so the pin was decorative: every cluster
# any evidence here was measured on came from `kind create cluster` with no image at all. Kind's
# own default is digest pinned in its source, so nothing was unsafe, but the workflow said it was
# pinning something it was not. Empty is kind's default, which is what a reader gets on a laptop.
kind create cluster --name "$CLUSTER" ${KIND_NODE_IMAGE:+--image "$KIND_NODE_IMAGE"} \
  --wait 180s >/dev/null
CONTEXT="kind-$CLUSTER"
h() { helm --kube-context "$CONTEXT" "$@"; }
k() { kubectl --context "$CONTEXT" "$@"; }

# What is actually serving, read from the pods rather than from what helm believes. A release
# that says deployed while nothing is ready is the case worth being able to see.
serving() {
  k get pods -l "$SELECTOR" -o json |
    python3 -c '
import json, sys
pods = json.load(sys.stdin)["items"]
ready = sum(
    1
    for pod in pods
    if all(c.get("ready") for c in pod["status"].get("containerStatuses", []))
    and pod["status"].get("phase") == "Running"
)
print(ready, len(pods))
'
}

echo "==> revision 1, healthy"
h install canary "$CHART" --wait --timeout 120s >/dev/null
R1_SERVING=$(serving)

echo "==> revision 2, broken, with --atomic"
# --wait is implied by --atomic and the timeout is short on purpose: the failure being measured
# is a deploy that never becomes ready, and waiting the default five minutes for it proves
# nothing extra.
set +e
h upgrade canary "$CHART" --set failure.neverReady=true --atomic --timeout 60s \
  > "$OUT/atomic-upgrade.out" 2>&1
ATOMIC_EXIT=$?
set -e
sleep 5
ATOMIC_SERVING=$(serving)
ATOMIC_REVISION=$(h list -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["revision"])')
ATOMIC_STATUS=$(h list -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["status"])')

{
  echo "\$ helm upgrade canary charts/deploy-canary --set failure.neverReady=true --atomic --timeout 60s"
  echo "# exit code: $ATOMIC_EXIT"
  echo
  cat "$OUT/atomic-upgrade.out"
  echo
  echo "--- after it gave up ---"
  echo "helm reports revision $ATOMIC_REVISION, status $ATOMIC_STATUS"
  echo "pods ready / total: $ATOMIC_SERVING"
  echo
  echo "--- helm history ---"
  h history canary
} > "$OUT/atomic-rolls-back-unattended.txt"
rm -f "$OUT/atomic-upgrade.out"

echo "==> revision 3, broken, WITHOUT --atomic, then rolled back by hand"
set +e
h upgrade canary "$CHART" --set failure.neverReady=true --wait --timeout 45s > /dev/null 2>&1
BARE_EXIT=$?
set -e
sleep 5
BROKEN_SERVING=$(serving)
BROKEN_REVISION=$(h list -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["revision"])')

# The operator's move. Rolling back to the revision helm itself names as the last deployed one,
# rather than to a number somebody remembered.
TARGET=$(h history canary -o json |
  python3 -c '
import json, sys
history = json.load(sys.stdin)
deployed = [entry for entry in history if entry["status"] == "deployed"]
print(deployed[-1]["revision"] if deployed else 1)
')
set +e
h rollback canary "$TARGET" --wait --timeout 120s > "$OUT/rollback.out" 2>&1
ROLLBACK_EXIT=$?
set -e
sleep 5
ROLLED_SERVING=$(serving)
ROLLED_REVISION=$(h list -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["revision"])')

{
  echo "\$ helm upgrade canary charts/deploy-canary --set failure.neverReady=true --wait --timeout 45s"
  echo "# exit code: $BARE_EXIT, and WITHOUT --atomic the broken revision stays"
  echo "helm reports revision $BROKEN_REVISION, pods ready / total: $BROKEN_SERVING"
  echo
  echo "\$ helm rollback canary $TARGET --wait --timeout 120s"
  echo "# exit code: $ROLLBACK_EXIT"
  cat "$OUT/rollback.out"
  echo
  echo "helm reports revision $ROLLED_REVISION, pods ready / total: $ROLLED_SERVING"
  echo
  echo "--- helm history, which is the evidence a rollback happened at all ---"
  h history canary
} > "$OUT/rollback-by-hand.txt"
rm -f "$OUT/rollback.out"

REVISIONS=$(h history canary -o json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')

# READY AND TOTAL, NOT READY ALONE. The first version of this recorded only the ready count
# while the broken revision stood, and it read 2: the same as healthy, because the OLD pods keep
# serving while a rolling update is stuck. A number that makes a broken deploy look identical to
# a working one is worse than no number. The stuck pod is the difference between the two.
cat > "$OUT/summary.json" <<JSON
{
  "healthy_pods_ready": $(echo "$R1_SERVING" | awk '{print $1}'),
  "healthy_pods_total": $(echo "$R1_SERVING" | awk '{print $2}'),

  "atomic_upgrade_exit_code": $ATOMIC_EXIT,
  "atomic_pods_ready_afterwards": $(echo "$ATOMIC_SERVING" | awk '{print $1}'),
  "atomic_pods_total_afterwards": $(echo "$ATOMIC_SERVING" | awk '{print $2}'),
  "atomic_release_status_afterwards": "$ATOMIC_STATUS",

  "bare_upgrade_exit_code": $BARE_EXIT,
  "pods_ready_while_the_broken_revision_stood": $(echo "$BROKEN_SERVING" | awk '{print $1}'),
  "pods_total_while_the_broken_revision_stood": $(echo "$BROKEN_SERVING" | awk '{print $2}'),

  "rollback_exit_code": $ROLLBACK_EXIT,
  "pods_ready_after_the_rollback": $(echo "$ROLLED_SERVING" | awk '{print $1}'),
  "pods_total_after_the_rollback": $(echo "$ROLLED_SERVING" | awk '{print $2}'),
  "revisions_in_the_history": $REVISIONS
}
JSON

echo
echo "==> written to docs/evidence/rollback:"
ls -1 "$OUT"
echo
cat "$OUT/summary.json"
