"""What happens when a deploy fails: unattended, and by hand.

Two claims, measured separately, because they answer different questions. `--atomic` is the
pipeline's answer: a failed upgrade puts the previous release back with nobody watching and the
command still exits non-zero, so the build goes red. `helm rollback` is the operator's answer at
three in the morning, when the deploy went out hours ago and `--atomic` is long past.

THE NUMBER THAT MATTERS IS READY AND TOTAL, NOT READY ALONE. The first version of the harness
recorded only the ready count while the broken revision stood, and it read 2: identical to
healthy, because the OLD pods keep serving while a rolling update is stuck. A measurement that
makes a broken deploy look the same as a working one is worse than no measurement. The stuck
extra pod is the entire difference.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence" / "rollback"
TRANSCRIPTS = ("atomic-rolls-back-unattended.txt", "rollback-by-hand.txt")


def summary() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_every_transcript_records_the_command_that_produced_it(name: str) -> None:
    first = (EVIDENCE / name).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("$ helm "), f"{name} opens with {first!r}"


def test_the_control_is_healthy_so_the_failures_mean_something() -> None:
    numbers = summary()
    assert numbers["healthy_pods_ready"] == 2
    assert numbers["healthy_pods_total"] == 2


def test_atomic_puts_the_previous_release_back_and_still_fails_the_build() -> None:
    """Both halves, and the second is the one people forget.

    An upgrade that repairs itself and exits zero is an upgrade nobody hears about. The exit code
    is what turns a silent self-heal into a red pipeline, and it is asserted here beside the
    recovery rather than assumed to follow from it.
    """
    numbers = summary()
    assert numbers["atomic_upgrade_exit_code"] != 0, (
        "the upgrade failed and exited zero, so a pipeline would have gone green over it"
    )
    assert numbers["atomic_pods_ready_afterwards"] == 2
    assert numbers["atomic_pods_total_afterwards"] == 2, (
        "a pod is left over, so this was not a clean rollback"
    )
    assert numbers["atomic_release_status_afterwards"] == "deployed"


def test_without_atomic_the_broken_revision_stands_and_a_pod_is_stuck() -> None:
    """The state `--atomic` exists to avoid, and how it is actually visible.

    Ready is 2 in both the healthy case and this one, because the old pods keep serving. The
    difference is the third pod, which is the new revision, and it never becomes ready.
    """
    numbers = summary()
    assert numbers["bare_upgrade_exit_code"] != 0
    assert numbers["pods_ready_while_the_broken_revision_stood"] == 2
    assert numbers["pods_total_while_the_broken_revision_stood"] == 3, (
        "there is no stuck pod, so either the rollout was not stuck or this measurement is not "
        "of the state it was written for"
    )
    stuck = int(numbers["pods_total_while_the_broken_revision_stood"]) - int(
        numbers["pods_ready_while_the_broken_revision_stood"]
    )
    assert stuck == 1


def test_a_rollback_by_hand_succeeds_and_leaves_the_cluster_serving() -> None:
    numbers = summary()
    assert numbers["rollback_exit_code"] == 0
    assert numbers["pods_ready_after_the_rollback"] == 2
    assert numbers["pods_total_after_the_rollback"] == 2


def test_the_history_records_both_failures_and_both_recoveries() -> None:
    """A rollback that leaves no trace is indistinguishable from never having deployed.

    `helm history` is the only place the sequence survives, and it is the question somebody
    actually has in an incident: what is running, and what was running before it.
    """
    numbers = summary()
    assert numbers["revisions_in_the_history"] == 5

    text = (EVIDENCE / "rollback-by-hand.txt").read_text(encoding="utf-8")
    assert text.count("failed") >= 2, "both broken upgrades should be in the history as failed"
    assert "Rollback to" in text, "the history does not record that a rollback happened"
    assert "deployed" in text, "no revision is current, which cannot be right"


def test_the_rollback_target_is_read_from_helm_and_not_remembered() -> None:
    """The harness rolls back to the revision helm names as the last deployed one.

    Rolling back to a hardcoded number is the version of this that works until the history has
    one more entry than somebody expected, which is exactly what happens after the first failed
    upgrade.
    """
    harness = (REPO / "scripts" / "measure_rollback.sh").read_text(encoding="utf-8")
    assert 'entry["status"] == "deployed"' in harness
    assert "helm rollback canary 1" not in harness, "the target is hardcoded"


def test_every_measured_number_is_present_and_the_count_is_asserted() -> None:
    numbers = summary()
    assert len(numbers) == 13, f"the summary has {len(numbers)} numbers, assert the new one"


@pytest.mark.cluster
def test_both_recoveries_still_work_on_a_cluster() -> None:
    """Needs kind, kubectl and helm. Creates a cluster, breaks a deploy twice, destroys it."""
    import subprocess

    script = REPO / "scripts" / "measure_rollback.sh"
    assert script.exists(), "the harness is missing, so this test proves nothing"

    result = subprocess.run(
        ["bash", str(script), "quayz-rollback-pytest"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]

    fresh = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    assert fresh["atomic_upgrade_exit_code"] != 0
    assert fresh["rollback_exit_code"] == 0
    assert fresh["pods_total_while_the_broken_revision_stood"] == 3
