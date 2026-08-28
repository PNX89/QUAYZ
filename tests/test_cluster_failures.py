"""Every failure in the taxonomy, produced against a real cluster and read off it.

The offline half reads what `scripts/measure_failures.sh` recorded, so it checks something real
on a machine with no cluster, no kind and no Docker. The `cluster` marked half creates a cluster,
produces all five states and destroys it again.

FIVE, WHICH IS A CORRECTION. This said four, and the CI job that runs it is called "every
failure, on a cluster made and destroyed here". The missing one was the image that cannot be
pulled, and it is the one that matters most to have measured: it is the only failure with no
container state at all to read, so every claim about which instrument finds it was a claim about
a case nobody had produced.

WHY THE TRANSCRIPTS ARE NOT BYTE COMPARED. They carry pod names with random suffixes, IP
addresses, ages and restart counts that depend on how long a step took. `summary.json` carries
only the outcomes, and that is what is diffed. The same distinction was learned the hard way in a
sibling repository, where an exact lock count was asserted and a Linux runner saw a different one
without the claim being any less true.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence" / "cluster"

#: The command each transcript must say produced it, and the number of pods that command asks
#: for. The command is duplicated here on purpose: it is a second opinion on a line the harness
#: writes, and the reason it is needed is that the harness used to build the two separately and
#: they had drifted. Two transcripts printed an install without the `--set replicaCount=1` that
#: produced them, and the test at the time checked only that the line began with `$ helm install`.
PRODUCED_BY = {
    "healthy.txt": ("$ helm install canary charts/deploy-canary --wait", 2),
    "alive-but-never-ready.txt": (
        "$ helm install canary charts/deploy-canary --set failure.neverReady=true",
        2,
    ),
    "crash-loop.txt": (
        "$ helm install canary charts/deploy-canary --set failure.crashLoop=true "
        "--set replicaCount=1",
        1,
    ),
    "killed-for-memory.txt": (
        "$ helm install canary charts/deploy-canary --set failure.outOfMemory=true "
        "--set replicaCount=1",
        1,
    ),
    "image-cannot-be-pulled.txt": (
        "$ helm install canary charts/deploy-canary --set failure.badImage=true "
        "--set replicaCount=1",
        1,
    ),
}
TRANSCRIPTS = tuple(PRODUCED_BY)


#: The instrument readings taken for every case. Named here so a case that quietly stops being
#: measured is a failure rather than a smaller dictionary nobody counted.
READINGS = (
    "phase",
    "restarted",
    "terminated_reason",
    "exit_code",
    "waiting_reason",
    "endpoints_ready",
    "endpoints_not_ready",
)


def summary() -> dict[str, Any]:
    """Any, because this is JSON written by a shell script and read back.

    Typed as dict[str, object] it needed a cast at every comparison and mypy narrowed two string
    literals into a non-overlapping equality check, which is a complaint about the assertion
    being obviously true rather than about the code.
    """
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    return loaded


def cases() -> dict[str, dict[str, Any]]:
    """Keyed by the failure names in src/quayz/failures.py, plus the healthy control.

    The join is the point: a taxonomy entry renamed without renaming the case it was measured
    from stops matching, and a test says so.
    """
    found: dict[str, dict[str, Any]] = summary()["cases"]
    return found


def case(name: str) -> dict[str, Any]:
    return cases()[name]


def transcript(name: str) -> str:
    return (EVIDENCE / name).read_text(encoding="utf-8")


def section(name: str, heading: str) -> list[str]:
    """The lines under one `--- heading ---` block of a transcript."""
    lines = transcript(name).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"--- {heading}"))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("--- "):
            break
        if line.strip():
            body.append(line)
    return body


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_every_transcript_records_the_command_that_produced_it(name: str) -> None:
    """A captured output that does not say how it was produced cannot support a claim about it."""
    first = transcript(name).splitlines()[0]
    assert first == PRODUCED_BY[name][0], f"{name} says it was produced by {first!r}"


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_the_recorded_command_agrees_with_the_pods_that_appeared(name: str) -> None:
    """The check the command line cannot fake, and the one that would have caught the drift.

    Asserting the text of a line against a copy of that same text catches a regression and
    nothing else: if the harness printed the wrong command again, and this file were updated to
    match, both would agree and both would be wrong. The pod count comes from `kubectl get pods`
    in the same transcript, so an install that says `--set replicaCount=1` and shows two pods is
    caught by the cluster rather than by a copy.
    """
    expected = PRODUCED_BY[name][1]
    pods = section(name, "kubectl get pods")
    assert len(pods) == expected, (
        f"{name} says it asked for {expected} pod(s) and shows {len(pods)}: {pods}"
    )


def test_the_control_is_healthy_so_the_failures_mean_something() -> None:
    """Without this every assertion below is satisfiable by a chart that never works at all."""
    control = case("healthy")
    assert control["endpoints_ready"] == 2
    assert control["endpoints_not_ready"] == 0
    assert control["phase"] == "Running"
    assert control["restarted"] is False
    assert control["terminated_reason"] is None


def test_a_crash_loop_and_an_oomkill_are_told_apart_by_one_field() -> None:
    """The repository's argument, as two numbers from a real cluster.

    Both produce CrashLoopBackOff with a climbing restart count. The reason and the exit code are
    the only things that differ, and they are the reason there is a controller here.
    """
    crash = case("crash loop")
    oom = case("killed for memory")
    assert crash["terminated_reason"] == "Error"
    assert crash["exit_code"] == 1
    assert oom["terminated_reason"] == "OOMKilled"
    assert oom["exit_code"] == 137
    # Compared through the dict rather than as two literals, so this asserts what the CLUSTER
    # produced rather than what is written two lines above.
    assert crash["terminated_reason"] != oom["terminated_reason"], (
        "the two failures reported the same reason, so the one field that separates them does "
        "not, and this repository has nothing to say"
    )
    # And everything a person sees FIRST is the same, which is the half that makes the field
    # above worth having.
    assert crash["waiting_reason"] == oom["waiting_reason"] == "CrashLoopBackOff"
    assert crash["restarted"] is True and oom["restarted"] is True
    assert crash["phase"] == oom["phase"] == "Running"


def test_the_logs_of_an_oomkilled_container_say_nothing_is_wrong() -> None:
    """Which is why a log-based detector reports health for a container the kernel killed.

    Counted rather than described: the number of log lines mentioning a problem is zero, against
    a crash loop whose single line is the message the process chose to print on its way out.
    """
    numbers = summary()
    assert numbers["oom_log_lines_mentioning_a_problem"] == 0
    assert numbers["crash_loop_log_lines"] >= 1


def test_a_pod_that_is_alive_and_never_ready_has_not_restarted() -> None:
    """The failure a dashboard shows green: Running, restart count zero, clean logs."""
    never_ready = case("alive but never ready")
    assert never_ready["restarted"] is False
    assert never_ready["endpoints_ready"] == 0
    assert never_ready["endpoints_not_ready"] == 2
    assert never_ready["phase"] == "Running"
    assert never_ready["terminated_reason"] is None
    assert never_ready["waiting_reason"] is None, (
        "there is no waiting reason to read for this one either, which is why the instrument "
        "that finds it is neither of the two fields the other failures are found on"
    )


def test_an_image_that_never_pulled_has_no_container_state_to_read() -> None:
    """The measurement the taxonomy was missing, and it contradicts what the table used to say.

    There is no terminated state at all, because the container never started and nothing has
    exited. `lastState.terminated.reason`, the field this repository is largely about, is empty
    here, and the evidence is on `state.waiting.reason` instead. A restart-count detector reads
    zero and reports health.
    """
    image = case("image cannot be pulled")
    assert image["terminated_reason"] is None, (
        "the image-pull failure now has a terminated state, so the claim that it has none is "
        "no longer true and the taxonomy needs rewriting rather than this test relaxing"
    )
    assert image["exit_code"] is None
    assert image["waiting_reason"] == "ImagePullBackOff"
    assert image["restarted"] is False


def test_the_pod_phase_separates_exactly_one_failure() -> None:
    """The taxonomy said pod phase separated nothing at all. It separates one, and only one.

    The image that cannot be pulled is Pending because no container ever started. Every other
    failure here, and the healthy control, is Running. So phase tells that one failure from the
    other four and tells the other four apart from nothing.
    """
    phases = {name: reading["phase"] for name, reading in cases().items()}
    assert phases["image cannot be pulled"] == "Pending"
    others = {name: phase for name, phase in phases.items() if name != "image cannot be pulled"}
    assert set(others.values()) == {"Running"}, (
        f"phase no longer separates the image pull from the rest: {phases}"
    )


def test_endpointslice_readiness_separates_failure_from_health_and_nothing_else() -> None:
    """The other correction, and the more embarrassing one.

    The taxonomy named EndpointSlice readiness as the instrument for "alive but never ready", as
    if it identified that failure. It does not. Every failure here reads zero endpoints ready,
    including the two that present as CrashLoopBackOff and the one whose container never
    started. What it separates is a Service that is serving from one that is not, which is worth
    knowing and is not the same claim.
    """
    ready = {name: reading["endpoints_ready"] for name, reading in cases().items()}
    failures = {name: value for name, value in ready.items() if name != "healthy"}
    assert set(failures.values()) == {0}, (
        f"a failure now has a ready endpoint, so this instrument does distinguish: {ready}"
    )
    assert ready["healthy"] > 0, (
        "the control has no ready endpoints either, so this measures a broken chart"
    )


def test_the_wide_output_lists_addresses_that_are_not_ready() -> None:
    """The trap this repository asserts against, measured rather than warned about.

    With no endpoint ready at all, `kubectl get endpointslice -o wide` still printed both pod
    addresses in its ENDPOINTS column. A test that counted that column would report a healthy
    Service for one serving nothing, so every readiness assertion here reads
    `.endpoints[*].conditions.ready` instead.
    """
    wide = int(summary()["never_ready_addresses_in_the_wide_column"])
    ready = int(case("alive but never ready")["endpoints_ready"])
    assert wide == 2
    assert ready == 0
    assert wide > ready, (
        "the wide column and the conditions agree here, so either the trap no longer exists or "
        "this measurement is not the one it was written for"
    )


def test_the_transcripts_show_the_disagreement_rather_than_describing_it() -> None:
    """So a reader sees it rather than being told about it.

    This used to assert two strings the harness echoes unconditionally into EVERY transcript, so
    it passed for any output the harness could produce, including one where the two instruments
    agreed. What is asserted now is the readings themselves: the conditions line says nothing is
    ready, and the wide output beneath it still prints an address.
    """
    for name in ("alive-but-never-ready.txt", "image-cannot-be-pulled.txt"):
        conditions = section(name, "EndpointSlice conditions")
        assert conditions[0].split() == ["ready", "notready"], conditions
        ready = int(conditions[1].split()[0])

        wide = "\n".join(section(name, "and the WIDE output"))
        # Counted with a pattern rather than by splitting on whitespace: the ENDPOINTS column
        # holds them comma separated, so `10.244.0.7,10.244.0.8` is ONE whitespace token and two
        # addresses. Counting tokens saw zero addresses in a line that plainly has two.
        addresses = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", wide)
        assert addresses, f"{name} shows no address at all in the wide output: {wide!r}"
        assert len(addresses) > ready, (
            f"{name}: the wide column lists {len(addresses)} address(es) and {ready} endpoint(s) "
            f"are ready. They agree, so the transcript does not show the disagreement it exists "
            f"to show"
        )


def test_every_case_carries_every_reading_and_the_counts_are_asserted() -> None:
    """The matrix has no holes, and a new column cannot arrive unchecked by the tests above.

    A hole is how the taxonomy went wrong: EndpointSlice readiness was named as the instrument
    for one failure because nobody had pointed it at the other four, where it reads the same.
    """
    numbers = summary()
    assert set(numbers) == {
        "cases",
        "crash_loop_log_lines",
        "oom_log_lines_mentioning_a_problem",
        "never_ready_addresses_in_the_wide_column",
    }
    assert set(cases()) == {
        "healthy",
        "alive but never ready",
        "crash loop",
        "killed for memory",
        "image cannot be pulled",
    }
    for name, reading in cases().items():
        assert tuple(reading) == READINGS, f"{name} carries {tuple(reading)}"


@pytest.mark.cluster
def test_the_failures_still_happen_on_a_cluster() -> None:
    """Needs kind, kubectl and helm. Creates a cluster, produces all five states, destroys it.

    Named after what it does rather than after the script: if the harness is renamed this fails
    to find it, which is better than a test that quietly stops running.
    """
    import subprocess

    script = REPO / "scripts" / "measure_failures.sh"
    assert script.exists(), "the harness is missing, so this test proves nothing"

    with_scratch = subprocess.run(
        ["bash", str(script), "quayz-pytest"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=900,
    )
    assert with_scratch.returncode == 0, with_scratch.stdout[-3000:] + with_scratch.stderr[-2000:]

    fresh = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))["cases"]
    assert fresh["killed for memory"]["terminated_reason"] == "OOMKilled"
    assert fresh["crash loop"]["terminated_reason"] == "Error"
    assert fresh["alive but never ready"]["restarted"] is False
    assert fresh["image cannot be pulled"]["terminated_reason"] is None
