#!/usr/bin/env bash
# Somebody changed the cluster by hand. When do you find out?
#
# Usage:  scripts/measure_drift.sh [cluster-name]
#
# THE FINDING THIS SCRIPT EXISTS TO RECORD, and it decided the design. `helm_release` does NOT
# detect object-level drift. Scale a Deployment from two replicas to five by hand and
# `terraform plan -detailed-exitcode` prints "No changes. Your infrastructure matches the
# configuration" and exits 0. It is not a bug: the resource compares the chart and its VALUES,
# and the values did not change. The cluster did.
#
# So the detector is `helm get values` piped through `helm template` into `kubectl diff`, which
# compares the DECLARED objects against the LIVE ones and catches the same edit exactly.
#
# TWO EXIT CODES, THREE MEANINGS, AND CONFLATING THEM IS THE WHOLE TRAP:
#
#   terraform plan -detailed-exitcode   0 no diff   2 diff   1 ERROR
#   kubectl diff                        0 no diff   1 diff  >1 ERROR
#
# An error is NOT the absence of drift. A drift check that treats a non-zero-but-not-the-diff-code
# as healthy reports a clean bill for a cluster it could not reach, and a sibling repository
# measured exactly that: with -refresh=false, terraform reports "No changes" and exits 0 against
# an API server that is not listening. So -refresh=false is never used here and every exit code
# is mapped explicitly.
set -euo pipefail

CLUSTER="${1:-quayz-drift}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/evidence/drift"
CHART="$ROOT/charts/deploy-canary"
TFDIR="$ROOT/terraform/cluster"
CONTEXT="kind-$CLUSTER"
KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"

for tool in kind kubectl helm terraform; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 1; }
done
mkdir -p "$OUT"

cleanup() { kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> creating cluster $CLUSTER"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --wait 180s >/dev/null

tf() {
  terraform -chdir="$TFDIR" "$@" \
    -var "kubeconfig=$KUBECONFIG_PATH" \
    -var "context=$CONTEXT" \
    -var "chart_path=$CHART"
}

# Every exit code named, so a reader can see there is no branch where an error is silently a
# pass. `set +e` around each, because the whole point is reading the code rather than dying on it.
plan_exit() {
  set +e
  terraform -chdir="$TFDIR" plan -detailed-exitcode -no-color -input=false \
    -var "kubeconfig=$KUBECONFIG_PATH" -var "context=$CONTEXT" -var "chart_path=$CHART" \
    > "$OUT/.plan.out" 2>&1
  local code=$?
  set -e
  echo "$code"
}

diff_exit() {
  helm --kube-context "$CONTEXT" get values canary -o json > "$OUT/.values.json" 2>/dev/null \
    || echo '{}' > "$OUT/.values.json"
  helm template canary "$CHART" -f "$OUT/.values.json" > "$OUT/.rendered.yaml" 2>/dev/null
  set +e
  kubectl --context "$CONTEXT" diff -f "$OUT/.rendered.yaml" > "$OUT/.diff.out" 2>&1
  local code=$?
  set -e
  echo "$code"
}

echo "==> terraform init and apply"
terraform -chdir="$TFDIR" init -input=false -no-color >/dev/null
tf apply -auto-approve -no-color -input=false >/dev/null

echo "==> baseline, nothing touched"
BASE_PLAN=$(plan_exit)
BASE_DIFF=$(diff_exit)

echo "==> somebody scales the deployment by hand"
kubectl --context "$CONTEXT" scale deployment canary-canary --replicas=5 >/dev/null
sleep 10
SCALED=$(kubectl --context "$CONTEXT" get deployment canary-canary -o jsonpath='{.spec.replicas}')
DRIFT_PLAN=$(plan_exit)
DRIFT_DIFF=$(diff_exit)

{
  echo "\$ kubectl scale deployment canary-canary --replicas=5"
  echo "\$ terraform plan -detailed-exitcode"
  echo "\$ helm get values canary | helm template | kubectl diff -f -"
  echo
  echo "the deployment is now running $SCALED replicas against 2 declared."
  echo
  echo "--- terraform plan -detailed-exitcode, exit $DRIFT_PLAN ---"
  cat "$OUT/.plan.out"
  echo
  echo "--- kubectl diff against the rendered chart, exit $DRIFT_DIFF ---"
  grep -E '^[+-] ' "$OUT/.diff.out" | grep -i replica || cat "$OUT/.diff.out"
  echo
  echo "helm_release compares the chart and its values. The values did not change; the cluster"
  echo "did. That is not a bug in the provider, it is the boundary of what the resource models,"
  echo "and a drift detector built on it alone cannot see the drift it exists to catch."
} > "$OUT/a-hand-edit-and-two-detectors.txt"

echo "==> an unreachable cluster must not look like no drift"
# The blocker the pre-flight found, reproduced here so it cannot come back: with -refresh=false
# terraform reports no changes against a cluster that is gone. The detector must treat an error
# as an error.
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
sleep 2
DEAD_PLAN=$(plan_exit)
set +e
terraform -chdir="$TFDIR" plan -detailed-exitcode -refresh=false -no-color -input=false \
  -var "kubeconfig=$KUBECONFIG_PATH" -var "context=$CONTEXT" -var "chart_path=$CHART" \
  > "$OUT/.norefresh.out" 2>&1
DEAD_NOREFRESH=$?
set -e

{
  echo "\$ terraform plan -detailed-exitcode                  # against a cluster that is gone"
  echo "\$ terraform plan -detailed-exitcode -refresh=false   # the same, made fast"
  echo
  echo "--- with a refresh, exit $DEAD_PLAN ---"
  tail -6 "$OUT/.plan.out"
  echo
  echo "--- WITH -refresh=false, exit $DEAD_NOREFRESH ---"
  grep -m4 'No changes\|Plan:\|Error' "$OUT/.norefresh.out" || tail -4 "$OUT/.norefresh.out"
  echo
  echo "That is the trap. The flag everybody adds to make a drift check fast turns a cluster"
  echo "that no longer exists into a clean bill of health. -refresh=false is never used here."
} > "$OUT/an-unreachable-cluster-is-not-clean.txt"

cat > "$OUT/summary.json" <<JSON
{
  "baseline_terraform_plan_exit": $BASE_PLAN,
  "baseline_kubectl_diff_exit": $BASE_DIFF,

  "hand_scaled_to": $SCALED,
  "after_hand_edit_terraform_plan_exit": $DRIFT_PLAN,
  "after_hand_edit_kubectl_diff_exit": $DRIFT_DIFF,

  "unreachable_cluster_plan_exit": $DEAD_PLAN,
  "unreachable_cluster_plan_exit_with_refresh_false": $DEAD_NOREFRESH
}
JSON

rm -f "$OUT"/.plan.out "$OUT"/.diff.out "$OUT"/.values.json "$OUT"/.rendered.yaml "$OUT"/.norefresh.out
echo
echo "==> written to docs/evidence/drift:"
ls -1 "$OUT"
echo
cat "$OUT/summary.json"
