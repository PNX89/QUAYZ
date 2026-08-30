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
# EVERY failure now, which is a correction. This produced four of the five in the taxonomy and
# the CI job was called "every failure". The one it skipped, an image that cannot be pulled, is
# the one whose evidence lives on a different field from all the others: there is no terminated
# state to read, because nothing ever ran. A taxonomy claim about an instrument that was never
# pointed at that failure is a claim nobody measured.
#
# THE TRANSCRIPT PRINTS THE COMMAND THAT PRODUCED IT, and that is also a correction. The flags
# were passed twice, once to helm and once to the echo, and they had drifted: two transcripts
# printed an install without the `--set replicaCount=1` that produced them. They come from one
# variable now, so they cannot disagree.
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

CONTEXT="kind-$CLUSTER"
k() { kubectl --context "$CONTEXT" "$@"; }
h() { helm --kube-context "$CONTEXT" "$@"; }

# The one field that separates a crash loop from an OOMKill, read as data rather than parsed
# out of a table. `kubectl get pods` shows the same thing for both.
#
# It prints the WAITING reason as well, because the failure with no terminated state has to be
# read off something, and that something is this. Four values, always four, so a caller can
# `awk` a fixed column without wondering whether a field was omitted.
state() {
  k get pods -l "$SELECTOR" -o json |
    python3 -c '
import json, sys
pods = json.load(sys.stdin)["items"]
for pod in pods:
    for status in pod["status"].get("containerStatuses", []):
        last = status.get("lastState", {}).get("terminated", {})
        waiting = status.get("state", {}).get("waiting", {})
        print(status["restartCount"],
              last.get("reason", "none"),
              last.get("exitCode", "none"),
              waiting.get("reason", "none"))
        raise SystemExit
print("0 none none none")
'
}

# The pod phase, which the taxonomy claimed separated nothing. It separates exactly one failure
# and the measurement is what settles it.
# THE PHASE, AND IT REFUSES TO ANSWER WHEN THE PODS DISAGREE.
#
# This read `.items[0]` and reported whatever kubectl listed first. With two replicas that is a
# coin whenever the two are in different phases, and with a leaking previous release it is a coin
# between two releases. Both are the same defect: a single value sampled from a set nobody
# checked was uniform.
#
# `disagreement:` is returned rather than a phase, so a settle predicate comparing against
# "Running" waits instead of passing, and a summary written with it in would be obviously wrong
# rather than plausibly wrong.
phase() {
  local seen
  seen=$(
    k get pods -l "$SELECTOR" -o jsonpath='{.items[*].status.phase}' 2>/dev/null |
      tr ' ' '\n' | sort -u | paste -sd, -
  )
  # printf without a newline, matching what the jsonpath read emitted. `record` writes this into
  # a transcript and adds its own blank line, so a trailing newline here puts two in the file.
  if [ -z "$seen" ]; then
    printf 'none'
  elif [ "${seen#*,}" != "$seen" ]; then
    printf 'disagreement:%s' "$seen"
  else
    printf '%s' "$seen"
  fi
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

addresses_in_the_wide_column() {
  k get endpointslice -l "kubernetes.io/service-name=canary-canary" -o wide --no-headers 2>/dev/null |
    awk '{print $4}' | tr ',' '\n' | grep -c . || true
}

# UNINSTALL AND THEN WAIT FOR THE PODS TO ACTUALLY BE GONE, which `sleep 3` did not do.
#
# THE RUN THAT PROVED THIS NECESSARY, and it is the second flake in this file with the same
# shape. A footer-only pull request recorded `"phase": "Failed"` for "alive but never ready" and
# turned the required check red. The never-ready pod cannot fail: it serves HTTP, its liveness
# probe passes, and only its readiness file is missing.
#
# What it read was a pod from the PREVIOUS case. `helm uninstall` returns as soon as the API
# server accepts the deletion, and the pods spend a few more seconds Terminating. Three seconds
# later the next release installs, and now the label selector matches two generations at once.
# Every observation here reads `.items[0]`, which is whichever kubectl happens to list first, so
# `settle` could be satisfied by the new pod while `observe`, moments later, read an old one
# whose container had just been killed and whose phase was therefore Failed.
#
# The predicate tightening that went in before this was correct and insufficient: it made
# `settle` wait for the right state, and said nothing about WHICH POD it was reading.
reset() {
  h uninstall canary >/dev/null 2>&1 || true
  local waited=0
  while [ "$waited" -lt 120 ]; do
    if [ "$(k get pods -l "$SELECTOR" --no-headers 2>/dev/null | grep -c .)" = "0" ]; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "pods from the previous case are still present after ${waited}s, so anything measured " >&2
  echo "now could be about either release" >&2
  k get pods -l "$SELECTOR" -o wide >&2 || true
  return 1
}

# WAIT FOR THE STATE THIS CASE IS ABOUT, RATHER THAN SLEEPING FOR A NUMBER SOMEBODY GUESSED.
#
# THE RUN THAT PROVED THIS NECESSARY. A fixed sleep samples the backoff cycle wherever it happens
# to land. The same harness that read a crash loop here as `waiting=CrashLoopBackOff, restarts=2,
# 1 log line` read it on a CI runner as `waiting=null, restarts=3, 0 log lines`, because there
# the container was RUNNING again at that instant: it had just restarted, so there was no waiting
# reason to read and the new container had not printed anything yet. All three went into the file
# CI diffs, and the diff is what caught it.
#
# So the loop below waits for a KIND of state rather than for a duration, and fails loudly if it
# never arrives. A harness that samples a moving system at an arbitrary moment is measuring the
# scheduler's timing and calling it a property of the failure.
settled() {
  local what="$1" restarts term exit_code waiting
  read -r restarts term exit_code waiting <<<"$(state)"
  case "$what" in
    healthy)
      # The control, which `helm install --wait` already settles. It has a predicate anyway
      # because `observe` re-checks after reading, and a control whose state is not checked is
      # the one case where a leaked pod from a previous release would go unnoticed.
      if [ "$restarts" = "0" ] && [ "$waiting" = "none" ] && [ "$(phase)" = "Running" ]; then
        local up down want
        read -r up down <<<"$(readiness)"
        want=$(k get deploy canary-canary -o jsonpath='{.spec.replicas}' 2>/dev/null)
        if [ "$down" = "0" ] && [ -n "$want" ] && [ "$up" = "$want" ]; then return 0; fi
      fi
      ;;
    backoff)
      # THREE HALVES NOW, AND THE THIRD ARRIVED AFTER A FLAKE. Waiting in backoff, a previous
      # termination to read, AND the phase this case is about.
      #
      # The phase was left out because a pod in CrashLoopBackOff is normally Running: the pod is
      # up and the container is restarting. That is true almost always, and "almost always" is
      # what this predicate exists to stop mattering. A footer-only pull request, changing no
      # code at all, recorded `"phase": "Failed"` here and turned the required check red, because
      # the predicate was satisfied at a moment the phase had not settled.
      #
      # The taxonomy claims the phase separates exactly ONE failure, the image pull. That claim
      # is only true if the crash-looping cases are Running, so the predicate now waits for the
      # state the claim is about rather than for two thirds of it.
      if [ "$waiting" = "CrashLoopBackOff" ] && [ "$term" != "none" ] && \
         [ "$exit_code" != "none" ] && [ "$(phase)" = "Running" ]
      then return 0; fi
      ;;
    imagepull)
      # ImagePullBackOff and NOT ErrImagePull, which is the same lesson one layer down.
      # ErrImagePull is what the kubelet reports on the FIRST failed pull and ImagePullBackOff is
      # where it settles, so accepting either records whichever the sampling happened to catch:
      # the first run of this waited for either and read ErrImagePull, where every earlier run
      # had read ImagePullBackOff.
      if [ "$waiting" = "ImagePullBackOff" ]; then return 0; fi
      ;;
    unready)
      # Running, nothing waiting, nothing restarted, and out of the Service. ContainerCreating is
      # the state this waits past.
      if [ "$restarts" = "0" ] && [ "$waiting" = "none" ] && [ "$(phase)" = "Running" ]; then
        local ready notready expected
        read -r ready notready <<<"$(readiness)"
        # EVERY REPLICA, NOT AT LEAST ONE, and `-ge 1` is what made this flake. The endpoints
        # controller adds addresses one at a time, so a predicate satisfied by the first one
        # records however many had appeared at that instant: the committed summary said 2 and a
        # rerun said 1, on a pull request that changed a README footer.
        #
        # What the case is about is a Deployment whose every pod is running and none of them
        # ready, so the predicate waits for as many not-ready addresses as there are pods.
        expected=$(k get deploy canary-canary -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
        if [ "$ready" = "0" ] && [ "$notready" -ge 1 ] && [ "$notready" = "$expected" ]; then
          return 0
        fi
      fi
      ;;
  esac
  return 1
}

settle() {
  local what="$1" waited=0
  while [ "$waited" -lt 150 ]; do
    if settled "$what"; then
      echo "    settled into $what after ${waited}s"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  echo "the cluster never reached the '$what' state this case is about, so there is nothing " >&2
  echo "here to measure and a summary written anyway would be believed" >&2
  k get pods -l "$SELECTOR" -o wide >&2 || true
  return 1
}

# One variable for the flags, used by the install AND by the transcript, so the recorded command
# is the command. FLAGS is a global set by `produce` immediately before it installs.
FLAGS=""
produce() {
  local timeout="$1"; shift
  FLAGS="$*"
  reset
  # shellcheck disable=SC2086
  h install canary "$CHART" $FLAGS --timeout "$timeout" >/dev/null 2>&1 || true
}

record() {
  local name="$1" file="$2"
  {
    echo "$ helm install canary charts/deploy-canary${FLAGS:+ $FLAGS}"
    echo "# failure: $name"
    echo
    echo "--- kubectl get pods ---"
    k get pods -l "$SELECTOR" --no-headers || true
    echo
    echo "--- pod phase ---"
    phase
    echo
    echo
    echo "--- the fields that separate them ---"
    echo "restartCount lastState.terminated.reason exitCode state.waiting.reason"
    state
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

# One record per case, and the SAME six instruments pointed at every one of them.
#
# Written this way because src/quayz/failures.py makes a claim per instrument about which
# failures it can and cannot see, and those claims were checked against nothing. A matrix with a
# hole in it is how "EndpointSlice readiness is the answer for alive but never ready" survived:
# nobody had pointed that instrument at a crash loop, where it reads exactly the same.
RECORDS=""

# OBSERVE, THEN CHECK THE STATE HAS NOT MOVED UNDER THE OBSERVATION.
#
# THE FLAKE THIS CLOSES, and it is the third in this file with the same shape. A footer-only pull
# request, changing no code at all, recorded `"phase": "Failed"` for "alive but never ready" and
# turned the required check red on main. That pod cannot fail on its own: it serves HTTP, its
# liveness probe passes, and the only thing missing is its readiness file.
#
# `settle` waits for the right state and then RETURNS, and every instrument below is a separate
# `kubectl` call made afterwards. Anything that moves in that window is recorded as though it
# were the settled state. The two previous fixes here both tightened the PREDICATE, which is
# necessary and cannot close this: the gap is between the predicate passing and the reading
# happening, not inside the predicate.
#
# What could move it was not reproduced on a laptop, where the pods of the previous release are
# gone within three seconds and the never-ready pod stays Running indefinitely. A CI runner is
# where it happened and a CI runner is under memory and disk pressure, where the kubelet evicts.
# So this does not diagnose the cause. It refuses to WRITE a measurement taken at a moment that
# had already moved, which is true whatever the cause turns out to be, and it names the case and
# both states so the next occurrence arrives as a fact rather than a mystery.
observe() {
  local case_name="$1" what="$2" before after
  before="$(phase)"
  RECORDS="${RECORDS}${case_name}"$'\t'"$(state)"$'\t'"$(readiness)"$'\t'"${before}"$'\n'
  after="$(phase)"
  if [ "$before" != "$after" ]; then
    echo "the phase moved from '$before' to '$after' while '$case_name' was being read, so the " >&2
    echo "record just taken describes no single moment" >&2
    k get pods -l "$SELECTOR" -o wide >&2 || true
    return 1
  fi
  if ! settled "$what"; then
    echo "'$case_name' no longer satisfies the '$what' predicate it settled into, so the record " >&2
    echo "just taken is of a state the case is not about" >&2
    k get pods -l "$SELECTOR" -o wide >&2 || true
    return 1
  fi
}

# SOURCEABLE, WHICH IS THE ONLY WAY THE PREDICATES ABOVE CAN BE EXECUTED RATHER THAN READ.
#
# Four tests watch the guards in this file and every assertion in all four was a substring search
# over this source. One of them looked for the prefix the mixed-phase branch prints, and the
# comment above `phase` explains that branch by naming the same prefix, so deleting the branch and
# keeping the comment left the suite green. The word is deliberately not repeated here for the
# same reason: a search for a guard's own name checks the spelling and not the guard.
#
# tests/test_failures.py sources this file with the variable below set, replaces `k` and `h` with
# stubs that answer from fixtures, and then calls `phase`, `settled` and `reset` for real. Nothing
# past this point runs in that mode, so no cluster is created and nothing is installed.
if [ -n "${MEASURE_FAILURES_SOURCE_ONLY:-}" ]; then
  return 0 2>/dev/null || exit 0
fi

for tool in kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 1; }
done
command -v python3 >/dev/null || { echo "python3 is not on PATH" >&2; exit 1; }
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

echo "==> healthy"
produce 120s --wait
observe "healthy" "healthy"
record "none, this is the control" "healthy.txt"

echo "==> alive but never ready"
produce 45s --set failure.neverReady=true
settle unready
observe "alive but never ready" "unready"
NEVERREADY_WIDE=$(addresses_in_the_wide_column)
record "alive but never ready" "alive-but-never-ready.txt"

echo "==> crash loop"
produce 45s --set failure.crashLoop=true --set replicaCount=1
settle backoff
observe "crash loop" "backoff"
# --previous, so this reads the container that DIED rather than the one that just started. Without
# it the count is whatever the new container had managed to print, which on a CI runner was
# nothing at all, and "the crash loop printed no reason either" is the opposite of the claim.
CRASH_LOGS=$(k logs -l "$SELECTOR" --previous --tail=20 2>/dev/null | grep -c . || true)
record "crash loop" "crash-loop.txt"

echo "==> killed for memory"
produce 45s --set failure.outOfMemory=true --set replicaCount=1
settle backoff
observe "killed for memory" "backoff"
# --previous for the same reason, and here it makes the claim STRONGER rather than merely
# reliable: these are the logs of the container the kernel actually killed, and they still say
# nothing is wrong.
OOM_LOGS=$(k logs -l "$SELECTOR" --previous --tail=20 2>/dev/null | grep -ci 'error\|fail\|kill' || true)
record "killed for memory" "killed-for-memory.txt"

echo "==> image cannot be pulled"
produce 45s --set failure.badImage=true --set replicaCount=1
settle imagepull
observe "image cannot be pulled" "imagepull"
record "image cannot be pulled" "image-cannot-be-pulled.txt"

# The decisive facts, and nothing that varies between runs. This is what CI diffs.
#
# THE CASE KEYS ARE THE FAILURE NAMES IN src/quayz/failures.py, on purpose. The taxonomy is
# checked against this file by joining on them, so a failure renamed in one place and not in the
# other fails a test rather than quietly comparing nothing.
#
# WRITTEN BY python3 RATHER THAN BY A HEREDOC, and that is a correction with teeth. The heredoc
# interpolated shell variables straight into JSON, so a container that had not terminated yet
# wrote the bare word `none` where a number belonged and the file was not JSON at all, while the
# script still exited 0. A second path did the same: `grep -c .` prints 0 AND exits 1 on empty
# input, so `|| echo 0` appended a second zero and the value spanned two lines.
#
# It also REFUSES to write a summary in which a measurement did not happen. A harness that
# records nothing and exits green is worse than one that fails, because the green is believed.
export RECORDS CRASH_LOGS OOM_LOGS NEVERREADY_WIDE

python3 - "$OUT/summary.json" <<'PYTHON'
import json, os, sys

# Read through the environment rather than through interpolation. Substituting shell variables
# into a Python heredoc is the same mistake in a different language: the value decides how the
# program parses.
def absent_is_none(value):
    return None if value == "none" else value

cases = {}
for line in os.environ["RECORDS"].strip().split("\n"):
    name, state, readiness, phase = line.split("\t")
    restarts, terminated_reason, exit_code, waiting_reason = state.split()
    ready, not_ready = readiness.split()
    cases[name] = {
        "phase": phase.strip(),
        # A BOOLEAN AND NOT THE COUNT, because the count is not stable and this file is diffed.
        # The same crash loop read 2 here and 3 on a CI runner, which is how long each waited
        # rather than which failure it was. The exact number is in the transcript for a reader;
        # what is asserted is whether anything restarted at all, which is the claim.
        "restarted": int(restarts) > 0,
        "terminated_reason": absent_is_none(terminated_reason),
        "exit_code": None if exit_code == "none" else int(exit_code),
        "waiting_reason": absent_is_none(waiting_reason),
        "endpoints_ready": int(ready),
        "endpoints_not_ready": int(not_ready),
    }

summary = {
    "cases": cases,
    "crash_loop_log_lines": int(os.environ["CRASH_LOGS"].split()[0]),
    "oom_log_lines_mentioning_a_problem": int(os.environ["OOM_LOGS"].split()[0]),
    "never_ready_addresses_in_the_wide_column": int(os.environ["NEVERREADY_WIDE"].split()[0]),
}

# What must have been measured, per case. A terminated_reason of None is a FINDING for the image
# pull and a failed run for the two that are supposed to have terminated, so it is named per case
# rather than required everywhere.
expected = {
    "healthy": (),
    "alive but never ready": (),
    "crash loop": ("terminated_reason", "exit_code"),
    "killed for memory": ("terminated_reason", "exit_code"),
    "image cannot be pulled": ("waiting_reason",),
}
missing = [
    f"{case}.{field}"
    for case, fields in expected.items()
    for field in fields
    if cases.get(case, {}).get(field) is None
]
if sorted(cases) != sorted(expected):
    missing.append(f"the cases produced were {sorted(cases)}")
if missing:
    print(f"these measurements did not happen: {missing}", file=sys.stderr)
    print("the cluster did not produce the state this run asked for, and a summary written "
          "anyway would be believed", file=sys.stderr)
    raise SystemExit(1)

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(summary, handle, indent=2)
    handle.write("\n")
PYTHON

echo
echo "==> written to docs/evidence/cluster:"
ls -1 "$OUT"
echo
cat "$OUT/summary.json"
