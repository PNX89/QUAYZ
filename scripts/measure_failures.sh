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

for tool in kind kubectl helm; do
  command -v "$tool" >/dev/null || { echo "$tool is not on PATH" >&2; exit 1; }
done
command -v python3 >/dev/null || { echo "python3 is not on PATH" >&2; exit 1; }
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
phase() {
  k get pods -l "$SELECTOR" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo "none"
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

reset() { h uninstall canary >/dev/null 2>&1 || true; sleep 3; }

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
    backoff)
      # Both halves: waiting in backoff AND a previous termination to read. Either alone is a
      # moment in the cycle rather than the state this repository makes claims about.
      if [ "$waiting" = "CrashLoopBackOff" ] && [ "$term" != "none" ] && [ "$exit_code" != "none" ]
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
        local ready notready
        read -r ready notready <<<"$(readiness)"
        if [ "$ready" = "0" ] && [ "$notready" -ge 1 ]; then return 0; fi
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
observe() {
  local case_name="$1"
  RECORDS="${RECORDS}${case_name}"$'\t'"$(state)"$'\t'"$(readiness)"$'\t'"$(phase)"$'\n'
}

echo "==> healthy"
produce 120s --wait
observe "healthy"
record "none, this is the control" "healthy.txt"

echo "==> alive but never ready"
produce 45s --set failure.neverReady=true
settle unready
observe "alive but never ready"
NEVERREADY_WIDE=$(addresses_in_the_wide_column)
record "alive but never ready" "alive-but-never-ready.txt"

echo "==> crash loop"
produce 45s --set failure.crashLoop=true --set replicaCount=1
settle backoff
observe "crash loop"
# --previous, so this reads the container that DIED rather than the one that just started. Without
# it the count is whatever the new container had managed to print, which on a CI runner was
# nothing at all, and "the crash loop printed no reason either" is the opposite of the claim.
CRASH_LOGS=$(k logs -l "$SELECTOR" --previous --tail=20 2>/dev/null | grep -c . || true)
record "crash loop" "crash-loop.txt"

echo "==> killed for memory"
produce 45s --set failure.outOfMemory=true --set replicaCount=1
settle backoff
observe "killed for memory"
# --previous for the same reason, and here it makes the claim STRONGER rather than merely
# reliable: these are the logs of the container the kernel actually killed, and they still say
# nothing is wrong.
OOM_LOGS=$(k logs -l "$SELECTOR" --previous --tail=20 2>/dev/null | grep -ci 'error\|fail\|kill' || true)
record "killed for memory" "killed-for-memory.txt"

echo "==> image cannot be pulled"
produce 45s --set failure.badImage=true --set replicaCount=1
settle imagepull
observe "image cannot be pulled"
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
