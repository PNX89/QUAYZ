"""The taxonomy, checked for the property that makes it worth having.

A list of ways a deploy can fail is easy to write and proves nothing. The claim here is sharper:
that some of them are indistinguishable by the instrument people reach for first, that the one
field which separates them is named, and that every one of those claims is a reading taken off a
real cluster rather than a sentence somebody was confident about.

THE JOIN IS THE POINT OF THIS FILE. Every entry in DETECTORS is checked against
`docs/evidence/cluster/summary.json`, which records the same six instruments pointed at the four
failures a cluster produces and at a healthy control. Five is the taxonomy's size and not the
matrix's, and MEASURED_ON_A_CLUSTER below is the difference between them. Before that join
existed, three of the six entries were wrong,
and every one of them was wrong in the direction that flattered the taxonomy:

  lastState.terminated.reason was credited with the image pull, which has no terminated state
  EndpointSlice readiness was credited with identifying one failure, and it reads zero for four
  pod phase was said to separate nothing, and it is the only instrument that finds the image pull

The first of those was load bearing: it was the ONLY entry naming the image pull, so the test
asserting every failure had an answer was green because of a false one.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from typing import Any

import pytest

from quayz.failures import DETECTORS, FAILURES, Detector, Failure, confusable_with

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "evidence" / "cluster"

#: The failures produced by scripts/measure_failures.sh, against which the instrument claims are
#: checked. `changed by hand afterwards` is not here: it is produced by scripts/measure_drift.sh
#: against a healthy cluster and is measured by test_drift.py instead.
MEASURED_ON_A_CLUSTER = (
    "image cannot be pulled",
    "crash loop",
    "killed for memory",
    "alive but never ready",
)

#: Every detector that names the columns it reads is joined to the matrix. Derived from the
#: table rather than listed here, so an entry cannot be added and silently go unchecked: the
#: hand-written version of this dictionary was one name away from exactly that.
JOINED = [entry for entry in DETECTORS if entry.fields]
UNJOINED = [entry for entry in DETECTORS if not entry.fields]


def reading(entry: Detector, case: dict[str, Any]) -> tuple[Any, ...]:
    """What one instrument reads on one case, as the tuple of columns it names."""
    return tuple(case[field] for field in entry.fields)


def cases() -> dict[str, dict[str, Any]]:
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    found: dict[str, dict[str, Any]] = loaded["cases"]
    return found


HARNESS = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_failures.sh"

#: scripts/measure_failures.sh with the cluster replaced by fixtures, so its predicates are RUN
#: rather than searched for. Everything they read goes through `k`, so replacing that one function
#: is the whole of the outside world as far as they are concerned. `sleep` goes with it: `reset`
#: waits two minutes for the previous release to disappear, which is right on a cluster and would
#: be two minutes of a unit suite.
PROBE = r"""
set -uo pipefail
export MEASURE_FAILURES_SOURCE_ONLY=1
# shellcheck source=/dev/null
. "$HARNESS"
set +e

k() {
  case "$*" in
    *endpointslice*) printf '%s\n' "$STUB_SLICES" ;;
    *status.phase*)  printf '%s'   "$STUB_PHASES" ;;
    *spec.replicas*) printf '%s'   "$STUB_REPLICAS" ;;
    *--no-headers*)  printf '%s'   "$STUB_PODLINES" ;;
    *)               printf '%s\n' "$STUB_PODS" ;;
  esac
}
h() { :; }
sleep() { :; }
"""


def pod(
    restarts: int = 0,
    terminated: str | None = None,
    exit_code: int | None = None,
    waiting: str | None = None,
) -> str:
    """One pod as `kubectl get pods -o json` prints it, carrying the four fields `state` reads."""
    status: dict[str, Any] = {"restartCount": restarts}
    if terminated is not None:
        status["lastState"] = {"terminated": {"reason": terminated, "exitCode": exit_code}}
    if waiting is not None:
        status["state"] = {"waiting": {"reason": waiting}}
    return json.dumps({"items": [{"status": {"containerStatuses": [status]}}]})


def endpoints(ready: int, not_ready: int) -> str:
    """An EndpointSlice as `readiness` reads it, which is the conditions and never the wide row."""
    entries = [{"conditions": {"ready": True}} for _ in range(ready)]
    entries += [{"conditions": {"ready": False}} for _ in range(not_ready)]
    return json.dumps({"items": [{"endpoints": entries}]})


def probe(call: str, **stubs: str) -> subprocess.CompletedProcess[str]:
    """Source the harness with fixtures for a cluster, then run one of its functions for real.

    THE REASON THIS EXISTS RATHER THAN ANOTHER SUBSTRING SEARCH. Every assertion the four tests
    below started with was a search over the harness's source, and one of them was satisfied by
    the comment sitting above the branch it was watching: delete the branch, keep the comment,
    suite green. Searching a file for a guard's name proves how it is spelled. These call it.
    """
    environment = {
        **os.environ,
        "HARNESS": str(HARNESS),
        "STUB_PHASES": "Running",
        "STUB_REPLICAS": "2",
        "STUB_PODLINES": "",
        "STUB_PODS": pod(),
        "STUB_SLICES": endpoints(ready=2, not_ready=0),
        **stubs,
    }
    return subprocess.run(
        ["bash", "-c", f"{PROBE}\n{call}"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )


def by_name(name: str) -> Failure:
    return next(failure for failure in FAILURES if failure.name == name)


def detector(name: str) -> Detector:
    return next(entry for entry in DETECTORS if entry.name == name)


def test_every_failure_and_detector_is_here_and_the_counts_are_asserted() -> None:
    """Five failures and nine detectors, and both counts are asserted so a change is deliberate.

    Both sets are spelled out rather than counted alone, because a count goes on passing when
    one entry is swapped for another. DETECTORS is the second half, added after deleting the
    entire `state.waiting.reason` row left every test in this file green: JOINED and UNJOINED
    are both derived from DETECTORS, so a missing row shrinks the parametrised sets rather than
    failing one of them, and nothing else in this file names an instrument that is not here. The
    gap only surfaced two files away, in test_capture.py and test_readme.py, over a total that
    moved for a reason neither file could say.
    """
    assert {failure.name for failure in FAILURES} == {
        "image cannot be pulled",
        "crash loop",
        "killed for memory",
        "alive but never ready",
        "changed by hand afterwards",
    }
    assert len(FAILURES) == 5

    assert {entry.name for entry in DETECTORS} == {
        "restart count",
        "container logs",
        "pod phase",
        "lastState.terminated.reason with exitCode",
        "state.waiting.reason",
        "EndpointSlice readiness",
        "every instrument at once, read together",
        "terraform plan over a helm_release",
        "the declared objects against the live ones",
    }
    assert len(DETECTORS) == 9


def test_every_failure_the_harness_produces_is_in_the_taxonomy_by_the_same_name() -> None:
    """The join, both ways. A name that matches nothing compares nothing and reports success."""
    measured = set(cases()) - {"healthy"}
    named = {failure.name for failure in FAILURES}
    assert measured <= named, f"the cluster produced {measured - named}, which nothing names"
    assert set(MEASURED_ON_A_CLUSTER) == measured


def test_a_crash_loop_and_an_oomkill_are_indistinguishable_by_symptom() -> None:
    """The argument. If these two ever stop colliding, this repository has less to say.

    They share `presents_as` exactly, which is the point: CrashLoopBackOff and a climbing
    restart count is what both produce, and it is what a person sees first.

    ALSO GUARDS confusable_with's OWN DOCSTRING, which named `looks_like` here once, though the
    function has always compared `presents_as`. Checked both ways: the docstring is read for the
    field it actually claims, and `looks_like` is shown to disagree for this very pair, which is
    exactly the derivation a reader who trusted the old docstring and "fixed" the code to match
    it would have broken.
    """
    assert by_name("crash loop").presents_as == by_name("killed for memory").presents_as
    assert confusable_with("crash loop") == ("killed for memory",)
    assert confusable_with("killed for memory") == ("crash loop",)

    doc = confusable_with.__doc__ or ""
    assert "presents_as" in doc.splitlines()[0], (
        "confusable_with's docstring no longer names the field it actually compares"
    )
    crash = by_name("crash loop")
    oom = by_name("killed for memory")
    assert crash.looks_like != oom.looks_like, (
        "looks_like now agrees for the confusable pair, so it no longer demonstrates why "
        "confusable_with must stay keyed on presents_as instead"
    )


def test_the_symptom_they_share_is_the_one_the_cluster_produced() -> None:
    """Otherwise `presents_as` is two strings that agree with each other and with nothing else."""
    crash = cases()["crash loop"]
    oom = cases()["killed for memory"]
    assert crash["waiting_reason"] == oom["waiting_reason"] == "CrashLoopBackOff"
    assert crash["restarted"] is True and oom["restarted"] is True
    assert "CrashLoopBackOff" in by_name("crash loop").presents_as
    assert "CrashLoopBackOff" in by_name("killed for memory").presents_as

    # looks_like is the sentence a reader gets rather than the derivation key, and it carried no
    # assertion beyond being non-empty: a mutation replacing it with "x" left the suite green.
    # It is what a person sees in `kubectl get pods`, which is the whole reason this pair is the
    # repository's argument, so the one fact worth pinning is that it says so for both of them.
    assert "CrashLoopBackOff" in by_name("crash loop").looks_like
    assert "CrashLoopBackOff" in by_name("killed for memory").looks_like


def test_confusability_is_symmetric() -> None:
    """Derived rather than listed, so it cannot disagree with itself.

    An earlier version compared the prose sentence instead of the symptom key and found no
    pairs at all, while the module's own docstring said two of them collide. A derivation over
    free text is a derivation over how somebody phrased it that morning.
    """
    for failure in FAILURES:
        for other in confusable_with(failure.name):
            assert failure.name in confusable_with(other), (
                f"{failure.name} is confusable with {other} and not the other way round"
            )


@pytest.mark.parametrize("entry", JOINED, ids=lambda e: e.name)
def test_each_instrument_notices_exactly_what_the_cluster_says_it_notices(entry: Detector) -> None:
    """The join, and the reason three entries in this table were wrong.

    An instrument NOTICES a failure when its reading differs from the reading it takes on the
    healthy control. That is the whole definition, applied to the recorded matrix, and nothing
    here is a judgement about what an instrument ought to be able to see.
    """
    healthy = reading(entry, cases()["healthy"])
    noticed = {name for name in MEASURED_ON_A_CLUSTER if reading(entry, cases()[name]) != healthy}
    assert noticed == set(entry.notices), (
        f"{entry.name} is recorded as noticing {sorted(entry.notices)} and the cluster says "
        f"{sorted(noticed)}"
    )


@pytest.mark.parametrize("entry", JOINED, ids=lambda e: e.name)
def test_each_instrument_separates_exactly_what_the_cluster_says_it_separates(
    entry: Detector,
) -> None:
    """SEPARATING IS NOT NOTICING, and conflating them is how this table came to flatter itself.

    An instrument separates a failure when, among the ones it notices, that one's reading is its
    own. The restart count used to be carved out here as "the interesting exception", reasoning
    that it notices two and reads 2 and 3, which are different numbers and not different kinds.
    That is not what the join below sees: `restarted` is recorded as a boolean, precisely so the
    count cannot leak into the comparison, and both crash loop and the OOMKill read `True`. The
    generic comparison already computes an empty separates set for it, matching the declared
    `()`, so the carve-out was removing coverage from the one entry it claimed needed protecting
    from a comparison that in fact agreed with it.
    """
    readings = {name: reading(entry, cases()[name]) for name in entry.notices}
    unique = {name for name, value in readings.items() if list(readings.values()).count(value) == 1}
    assert unique == set(entry.separates), (
        f"{entry.name} is recorded as separating {sorted(entry.separates)} and its readings "
        f"{readings} separate {sorted(unique)}"
    )


def test_the_instrument_people_reach_for_first_cannot_separate_the_pair() -> None:
    """Restart count sees both and tells you nothing about which.

    This is the sentence the controller in this repository exists to answer, so it is asserted
    rather than left in prose: the detector covers both members of a confusable pair, which
    means it FIRES on both and SEPARATES neither.
    """
    restarts = detector("restart count")
    assert set(restarts.notices) == {"crash loop", "killed for memory"}
    assert restarts.separates == ()
    assert set(restarts.notices) == set(confusable_with("crash loop")) | {"crash loop"}


def test_one_named_field_does_separate_them() -> None:
    """And it is a field, not a heuristic, so a test can read it off a live pod."""
    reason = detector("lastState.terminated.reason with exitCode")
    assert set(reason.separates) == {"crash loop", "killed for memory"}

    oom = by_name("killed for memory")
    assert "OOMKilled" in oom.told_apart_by
    assert "137" in oom.told_apart_by, (
        "the exit code is the second half of the evidence and a reader should not have to know it"
    )


def test_the_field_that_separates_the_pair_is_blank_for_two_other_failures() -> None:
    """The correction. It was credited with finding the image pull, which never terminates.

    A field that is empty cannot be the field a failure is told apart by, and this one is empty
    for two of the five: the image that never pulled, and the pod that is alive and never ready.
    """
    reason = detector("lastState.terminated.reason with exitCode")
    assert "image cannot be pulled" not in reason.notices
    assert "alive but never ready" not in reason.notices
    for name in ("image cannot be pulled", "alive but never ready"):
        assert cases()[name]["terminated_reason"] is None
        assert cases()[name]["exit_code"] is None


def test_logs_cannot_see_an_oomkill_at_all() -> None:
    """The trap that makes a log-based detector worse than useless here.

    The kernel takes the process away, so the logs end mid-sentence with nothing wrong in them.
    A detector reading logs reports health for a container that was killed.
    """
    logs = detector("container logs")
    assert "killed for memory" not in logs.notices
    assert "mid-sentence" in by_name("killed for memory").why_the_obvious_check_fails

    numbers = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    assert numbers["oom_log_lines_mentioning_a_problem"] == 0
    assert numbers["crash_loop_log_lines"] >= 1


def test_the_only_failure_a_dashboard_shows_green_is_named_as_such() -> None:
    """Alive but never ready is the one that passes every health check and serves nothing."""
    never_ready = by_name("alive but never ready")
    assert never_ready.presents_as == "Running with restart count zero"
    assert "green" in never_ready.why_the_obvious_check_fails


def test_the_failure_a_dashboard_shows_green_is_found_by_elimination_and_says_so() -> None:
    """The second correction, and the most flattering one.

    EndpointSlice readiness was named as this failure's distinguishing instrument, as if it
    identified it. It reads zero endpoints ready for all four failures on the cluster, so what
    it identifies is a Service that is not serving. This failure is the one with no positive
    evidence anywhere, which is exactly why it is the dangerous one, and the entry says so.
    """
    endpoints = detector("EndpointSlice readiness")
    assert len(endpoints.notices) == 4
    assert endpoints.separates == ()

    never_ready = cases()["alive but never ready"]
    assert never_ready["restarted"] is False
    assert never_ready["terminated_reason"] is None
    assert never_ready["waiting_reason"] is None
    assert never_ready["phase"] == "Running"
    assert "ELIMINATION" in by_name("alive but never ready").told_apart_by


def test_pod_phase_separates_the_one_failure_nothing_else_can_find_alone() -> None:
    """The third correction. This entry was empty, and empty was written down on purpose.

    The comment said an empty tuple was better than a missing one because absent reads as "not
    considered". It was considered, and the answer was wrong: Pending against Running is the
    only reading that names the image pull without reading anything else.
    """
    phase = detector("pod phase")
    assert phase.notices == ("image cannot be pulled",)
    assert phase.separates == ("image cannot be pulled",)
    assert cases()["image cannot be pulled"]["phase"] == "Pending"
    assert {cases()[name]["phase"] for name in cases() if name != "image cannot be pulled"} == {
        "Running"
    }


def test_a_plan_over_a_helm_release_is_recorded_as_finding_nothing() -> None:
    """The drift finding, in the same table as everything else rather than only in an ADR.

    A detector with an empty `notices` looks like an oversight and is the measurement: the plan
    exited 0 against a Deployment hand-scaled from two replicas to five, because the resource
    compares the chart and its values and neither had changed.
    """
    plan = detector("terraform plan over a helm_release")
    assert plan.notices == ()
    assert "exit 0" in plan.measured

    drift = json.loads((EVIDENCE.parent / "drift" / "summary.json").read_text(encoding="utf-8"))
    assert drift["after_hand_edit_terraform_plan_exit"] == 0
    assert drift["after_hand_edit_kubectl_diff_exit"] != 0


def test_every_detector_names_failures_that_exist() -> None:
    """A detector claiming to catch something not in the taxonomy is a stale entry."""
    names = {failure.name for failure in FAILURES}
    for entry in DETECTORS:
        unknown = (set(entry.notices) | set(entry.separates)) - names
        assert unknown == set(), f"{entry.name} claims {unknown}, which is not a failure"


def test_nothing_is_recorded_as_separated_without_being_noticed() -> None:
    """An instrument cannot tell apart a failure it does not see at all."""
    for entry in DETECTORS:
        assert set(entry.separates) <= set(entry.notices), (
            f"{entry.name} separates {set(entry.separates) - set(entry.notices)} without "
            f"noticing it"
        )


def test_the_three_unjoined_instruments_are_the_three_that_cannot_be_joined() -> None:
    """An entry with no columns is unchecked, so which ones they are is asserted rather than left.

    Two of them need a cluster somebody has edited by hand, which a different harness produces
    and test_drift.py checks. The third reads container logs, which the matrix counts for two
    cases rather than tabulating for five, and which test_logs_cannot_see_an_oomkill_at_all
    checks against those counts.
    """
    assert {entry.name for entry in UNJOINED} == {
        "container logs",
        "terraform plan over a helm_release",
        "the declared objects against the live ones",
    }


def test_no_single_field_finds_the_failure_a_dashboard_shows_green() -> None:
    """The sharpest thing this taxonomy has to say, and it was not being said.

    Every other failure is named by one field. This one is not: its row differs from the healthy
    control in exactly one column, endpoints ready, and that column reads identically for all
    four failures. So the only entry that separates it is the one that reads every instrument
    and finds only one of them abnormal, which is what the controller does when it falls through
    every branch and lands on never-ready.
    """
    single_field = [entry for entry in DETECTORS if len(entry.fields) == 1]
    assert single_field, "nothing here reads a single column, so this proves nothing"
    for entry in single_field:
        assert "alive but never ready" not in entry.separates, (
            f"{entry.name} now names it on its own, which is a better world and a different "
            f"claim: rewrite the entry rather than deleting this test"
        )
    combined = detector("every instrument at once, read together")
    assert "alive but never ready" in combined.separates


def test_every_failure_is_separated_by_at_least_one_instrument() -> None:
    """Stronger than the test this replaces, which asked only that something NOTICED each one.

    That weaker question was answered yes for the image pull by a false entry: the only detector
    naming it was the one field it is guaranteed not to appear on. Noticing is not an answer. A
    failure nothing can tell apart from another failure is a gap, and this asks for the
    instrument that names it.
    """
    separated = {name for entry in DETECTORS for name in entry.separates}
    missing = {failure.name for failure in FAILURES} - separated
    assert missing == set(), f"nothing here tells {missing} apart from anything else"


@pytest.mark.parametrize("entry", DETECTORS, ids=lambda e: e.name)
def test_every_instrument_says_what_it_actually_read(entry: Detector) -> None:
    """A table of opinions is the thing this file exists to stop being."""
    assert len(entry.measured) > 60, entry.name


@pytest.mark.parametrize("failure", FAILURES, ids=lambda f: f.name)
def test_every_entry_says_why_the_obvious_check_fails(failure: Failure) -> None:
    """The field that makes this a taxonomy rather than a list of things that can go wrong."""
    assert len(failure.why_the_obvious_check_fails) > 60, failure.name
    assert len(failure.told_apart_by) > 30, failure.name
    # what_happens and looks_like used to be asserted only for truthiness, which any non-empty
    # string satisfies: a mutation replacing every value of both fields with "x" left the suite
    # green, including for "CrashLoopBackOff and a climbing restart count, identical to a crash
    # loop", the sentence this repository's central pair depends on. why_the_obvious_check_fails
    # and told_apart_by already get the same treatment above; these two did not.
    assert len(failure.what_happens) > 20, failure.name
    assert len(failure.looks_like) > 20, failure.name
    assert failure.presents_as


def test_the_settle_predicates_wait_for_the_state_each_claim_is_about() -> None:
    """A FLAKE FOUND BY A PULL REQUEST THAT CHANGED A README FOOTER.

    Two predicates were satisfied before the state they describe had settled, so the committed
    summary moved on a rerun that changed no code:

        "phase": "Running" became "Failed" for a crash-looping pod
        never_ready_addresses_in_the_wide_column went from 2 to 1

    Both are the same mistake in different clothes: accepting a state that is ALMOST the one the
    claim is about. A pod in CrashLoopBackOff is normally Running, and the endpoints controller
    normally has both addresses by then, and "normally" is exactly what a predicate exists to
    stop mattering.

    This asserts the harness still waits for the whole state rather than most of it, because the
    fix lives in a shell script that nothing else here reads.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    harness = (repo / "scripts" / "measure_failures.sh").read_text(encoding="utf-8")
    flattened = " ".join(harness.split())

    assert '[ "$exit_code" != "none" ] &&' in flattened, (
        "the crash-loop predicate no longer waits for the phase, so it can record whatever the "
        "phase happened to be at that instant"
    )
    backoff = harness.split("backoff)")[1].split(";;")[0]
    assert '[ "$(phase)" = "Running" ]' in backoff, (
        "the crash-loop branch does not check the phase, so it can settle at a moment the pod "
        "reads Failed and record that"
    )
    assert '[ "$notready" = "$expected" ]' in flattened, (
        "the never-ready predicate no longer waits for every replica's address, so it records "
        "however many the endpoints controller had added by then"
    )
    assert "jsonpath='{.spec.replicas}'" in harness, (
        "the expected address count is no longer read from the Deployment, so it is a constant "
        "somebody will have to remember to change"
    )


def test_the_harness_refuses_a_reading_taken_at_a_moment_that_had_moved() -> None:
    """THE THIRD FLAKE IN THIS HARNESS, and the first fix that is not another predicate.

    A pull request changing only a README footer recorded `"phase": "Failed"` for "alive but
    never ready" and turned the required check red on main. That pod cannot fail on its own: it
    serves HTTP, its liveness probe passes, and the only thing missing is its readiness file.

    The two previous fixes both tightened the settle predicate, and neither could close this. The
    gap is not inside the predicate, it is AFTER it: `settle` waits for the right state and then
    returns, and each of the four instruments is a separate `kubectl` call made afterwards.
    Anything that moves in that window is recorded as though it were the settled state.

    The cause was not reproduced on a laptop, where the previous release's pods are gone within
    three seconds and a never-ready pod stays Running indefinitely. It happened on a CI runner,
    which is under memory and disk pressure where the kubelet evicts. So the harness does not
    claim a diagnosis. It refuses to WRITE a measurement taken at a moment that had already
    moved, which holds whatever the cause turns out to be, and it names the case and both states
    so a recurrence arrives as a fact rather than a mystery.

    Verified by forcing it: making `observe` believe it had read "Running" produced
    `the phase moved from 'Running' to 'Pending' while 'image cannot be pulled' was being read`
    and no summary was written.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    harness = (repo / "scripts" / "measure_failures.sh").read_text(encoding="utf-8")
    flattened = " ".join(harness.split())

    assert 'after="$(phase)"' in flattened and 'if [ "$before" != "$after" ]' in flattened, (
        "observe no longer re-reads the phase after recording it, so a state that moves while "
        "the four instruments are being read is written down as though it had settled"
    )
    assert 'if ! settled "$what"; then' in flattened, (
        "observe no longer re-checks the predicate the case settled into, so it can record a "
        "state the case is not about"
    )

    # Every observe call passes the predicate its case settled into, or the re-check above is
    # comparing against nothing. Read from the call sites rather than trusted.
    calls = re.findall(r"^observe \"([^\"]+)\" \"([^\"]+)\"$", harness, re.MULTILINE)
    assert dict(calls) == {
        "healthy": "healthy",
        "alive but never ready": "unready",
        "crash loop": "backoff",
        "killed for memory": "backoff",
        "image cannot be pulled": "imagepull",
    }, f"the observe calls no longer name a predicate each: {calls}"

    for _, predicate in calls:
        assert f"    {predicate})" in harness, (
            f"observe passes '{predicate}' and `settled` has no arm for it, so the re-check "
            f"falls through and always fails"
        )


def test_the_harness_waits_for_the_previous_release_to_be_gone() -> None:
    """`reset` was `helm uninstall; sleep 3`, which is a guess about how long deletion takes.

    `helm uninstall` returns when the API server accepts the deletion, not when the pods are
    gone, so the label selector can match two generations at once. Every instrument here reads
    `.items[0]`, whichever kubectl lists first, so a leaked pod is not merely present: it can be
    the one being measured.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    harness = (repo / "scripts" / "measure_failures.sh").read_text(encoding="utf-8")
    flattened = " ".join(harness.split())

    assert "sleep 3; }" not in flattened, "reset is back to guessing a duration"
    assert "reset() { h uninstall canary" in flattened
    assert "pods from the previous case are still present after" in flattened, (
        "reset no longer fails loudly when the previous release outlives its timeout, so it "
        "would fall through into a measurement of two releases at once"
    )


def test_the_phase_refuses_to_answer_when_the_pods_disagree() -> None:
    """`.items[0]` is a coin whenever two pods are in different phases.

    The default is two replicas, so this is not a corner case: it is every reading of every
    case that does not set replicaCount to one. A single value sampled from a set nobody
    checked was uniform is the same defect as a fixed sleep, one layer along.

    RUN RATHER THAN READ, WHICH IS THE SECOND CORRECTION THIS TEST HAS TAKEN. It used to search
    the harness for the prefix the mixed branch prints, and the comment explaining that branch
    names the same prefix, so the whole branch could be deleted with this test still green. It
    calls `phase` now, against pods whose phases disagree, and reads what comes back.
    """
    mixed = probe("phase", STUB_PHASES="Pending Running")
    assert mixed.stdout == "disagreement:Pending,Running", (
        "a mixed set of phases no longer produces a value that fails every predicate, so it "
        f"would be sampled instead of waited out: {mixed.stdout!r} {mixed.stderr[-400:]}"
    )
    agreed = probe("phase", STUB_PHASES="Running Running")
    assert agreed.stdout == "Running", (
        f"two pods that agree no longer read as one phase: {agreed.stdout!r}"
    )
    absent = probe("phase", STUB_PHASES="")
    assert absent.stdout == "none", (
        f"no pods at all reads as an empty string, which compares equal to nothing: "
        f"{absent.stdout!r}"
    )


def test_the_crash_loop_predicate_refuses_a_phase_that_had_not_settled() -> None:
    """THE FLAKE, EXECUTED. A footer-only pull request recorded `"phase": "Failed"` here.

    The three halves of the backoff predicate are waiting in backoff, a previous termination to
    read, and the phase this case is about. The test that watched them searched the harness for
    their text, so an early `return 0` at the top of `settled` left every predicate vacuously
    true and the suite green. This one asks the predicate, with the phase moved and everything
    else right.
    """
    crashing = pod(restarts=2, terminated="Error", exit_code=1, waiting="CrashLoopBackOff")
    settled = probe("settled backoff", STUB_PODS=crashing, STUB_PHASES="Running")
    assert settled.returncode == 0, (
        f"the crash loop this repository measures no longer satisfies its own predicate: "
        f"{settled.stderr[-400:]}"
    )
    moved = probe("settled backoff", STUB_PODS=crashing, STUB_PHASES="Failed")
    assert moved.returncode != 0, (
        "the crash-loop predicate accepts a pod that reads Failed, so it can record whatever "
        "the phase happened to be at that instant, which is the flake this fix was for"
    )


def test_the_never_ready_predicate_waits_for_every_replicas_address() -> None:
    """`-ge 1` is what made this flake, and the count comes off the Deployment rather than a 2.

    The endpoints controller adds addresses one at a time, so a predicate satisfied by the first
    records however many had appeared at that instant: the committed summary said 2 and a rerun
    said 1. The third case below is the half a text search cannot reach at all, because a
    hard-coded 2 and a value read from `.spec.replicas` are the same characters in a summary.
    """
    every = probe("settled unready", STUB_SLICES=endpoints(ready=0, not_ready=2))
    assert every.returncode == 0, (
        f"every replica out of the Service no longer settles the case: {every.stderr[-400:]}"
    )
    partial = probe("settled unready", STUB_SLICES=endpoints(ready=0, not_ready=1))
    assert partial.returncode != 0, (
        "one address out of two satisfies the never-ready predicate, so it records however many "
        "the endpoints controller had added by then"
    )
    three = probe("settled unready", STUB_SLICES=endpoints(ready=0, not_ready=3), STUB_REPLICAS="3")
    assert three.returncode == 0, (
        "a three-replica Deployment with three unready addresses does not settle, so the count "
        "is a constant somebody will have to remember to change rather than the Deployment's own"
    )


def test_reset_fails_rather_than_measuring_two_releases_at_once() -> None:
    """`helm uninstall; sleep 3` is a guess about how long deletion takes, and it was wrong.

    The test that replaced it asserted the guessed duration was absent from the source, which
    `sleep 3; return 0` evades while doing exactly the same thing. This runs `reset` instead,
    against pods that never go away, and requires it to fail loudly.
    """
    gone = probe("reset", STUB_PODLINES="")
    assert gone.returncode == 0, f"reset never returns on a clean cluster: {gone.stderr[-400:]}"
    lingering = probe("reset", STUB_PODLINES="canary-canary-59d 1/1 Terminating 4s")
    assert lingering.returncode != 0, (
        "reset returns success while the previous release's pods are still listed, so the next "
        "case is measured with two generations matching the same selector"
    )
    assert "still present after" in lingering.stderr, (
        f"reset fails silently, so the run stops with nothing saying why: {lingering.stderr!r}"
    )
