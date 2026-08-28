"""Every failure in the taxonomy, produced against a real cluster and read off it.

The offline half reads what `scripts/measure_failures.sh` recorded, so it checks something real
on a machine with no cluster, no kind and no Docker. The `cluster` marked half creates a cluster,
produces all four states and destroys it again.

WHY THE TRANSCRIPTS ARE NOT BYTE COMPARED. They carry pod names with random suffixes, IP
addresses, ages and restart counts that depend on how long a step took. `summary.json` carries
only the outcomes, and that is what is diffed. The same distinction was learned the hard way in a
sibling repository, where an exact lock count was asserted and a Linux runner saw a different one
without the claim being any less true.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence" / "cluster"
TRANSCRIPTS = (
    "healthy.txt",
    "alive-but-never-ready.txt",
    "crash-loop.txt",
    "killed-for-memory.txt",
)


def summary() -> dict[str, Any]:
    """Any, because this is JSON written by a shell script and read back.

    Typed as dict[str, object] it needed a cast at every comparison and mypy narrowed two string
    literals into a non-overlapping equality check, which is a complaint about the assertion
    being obviously true rather than about the code.
    """
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_every_transcript_records_the_command_that_produced_it(name: str) -> None:
    """A captured output that does not say how it was produced cannot support a claim about it."""
    first = (EVIDENCE / name).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("$ helm install"), f"{name} opens with {first!r}"


def test_the_control_is_healthy_so_the_failures_mean_something() -> None:
    """Without this every assertion below is satisfiable by a chart that never works at all."""
    numbers = summary()
    assert numbers["healthy_endpoints_ready"] == 2
    assert numbers["healthy_endpoints_not_ready"] == 0


def test_a_crash_loop_and_an_oomkill_are_told_apart_by_one_field() -> None:
    """The repository's argument, as two numbers from a real cluster.

    Both produce CrashLoopBackOff with a climbing restart count. The reason and the exit code are
    the only things that differ, and they are the reason there is a controller here.
    """
    numbers = summary()
    assert numbers["crash_loop_reason"] == "Error"
    assert numbers["crash_loop_exit_code"] == 1
    assert numbers["oom_reason"] == "OOMKilled"
    assert numbers["oom_exit_code"] == 137
    # Compared through the dict rather than as two literals, so this asserts what the CLUSTER
    # produced rather than what is written two lines above.
    assert numbers["crash_loop_reason"] != numbers["oom_reason"], (
        "the two failures reported the same reason, so the one field that separates them does "
        "not, and this repository has nothing to say"
    )


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
    numbers = summary()
    assert numbers["never_ready_restart_count"] == 0
    assert numbers["never_ready_endpoints_ready"] == 0
    assert numbers["never_ready_endpoints_not_ready"] == 2


def test_the_wide_output_lists_addresses_that_are_not_ready() -> None:
    """The trap this repository asserts against, measured rather than warned about.

    With no endpoint ready at all, `kubectl get endpointslice -o wide` still printed both pod
    addresses in its ENDPOINTS column. A test that counted that column would report a healthy
    Service for one serving nothing, so every readiness assertion here reads
    `.endpoints[*].conditions.ready` instead.
    """
    numbers = summary()
    wide = int(numbers["never_ready_addresses_in_the_wide_column"])
    ready = int(numbers["never_ready_endpoints_ready"])
    assert wide == 2
    assert ready == 0
    assert wide > ready, (
        "the wide column and the conditions agree here, so either the trap no longer exists or "
        "this measurement is not the one it was written for"
    )


def test_the_transcripts_carry_the_wide_output_beside_the_conditions() -> None:
    """So a reader sees the disagreement rather than being told about it."""
    text = (EVIDENCE / "alive-but-never-ready.txt").read_text(encoding="utf-8")
    assert "must not be trusted" in text
    assert "EndpointSlice conditions" in text


def test_every_measured_number_is_present_and_the_count_is_asserted() -> None:
    """Eleven, so a twelfth cannot arrive unchecked by the assertions above."""
    numbers = summary()
    assert len(numbers) == 12, f"the summary has {len(numbers)} numbers, assert the new one"


@pytest.mark.cluster
def test_the_failures_still_happen_on_a_cluster() -> None:
    """Needs kind, kubectl and helm. Creates a cluster, produces all four states, destroys it.

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

    fresh = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    assert fresh["oom_reason"] == "OOMKilled"
    assert fresh["crash_loop_reason"] == "Error"
    assert fresh["never_ready_restart_count"] == 0
