"""Somebody changed the cluster by hand. When do you find out?

THE FINDING THAT DECIDED THE DESIGN, and it is the reason there is a second detector here.
`helm_release` does not detect object-level drift. Scale a Deployment from two replicas to five
by hand and `terraform plan -detailed-exitcode` prints "No changes. Your infrastructure matches
the configuration" and exits 0. That is not a bug in the provider: the resource compares the
chart and its values, and the values did not change. The cluster did.

So the detector is the declared objects against the live ones, which is `helm get values` through
`helm template` into `kubectl diff`, and it catches the same edit exactly.

AND AN ERROR IS NOT THE ABSENCE OF DRIFT. Both instruments use a non-zero code for "found
something", so the failure mode is treating any non-zero as a diff or any zero as health. With
`-refresh=false`, terraform reports no changes and exits 0 against a cluster that has been
deleted, which is the one result that must never be read as a clean bill.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence" / "drift"
TRANSCRIPTS = ("a-hand-edit-and-two-detectors.txt", "an-unreachable-cluster-is-not-clean.txt")


def summary() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("name", TRANSCRIPTS)
def test_every_transcript_records_the_commands_that_produced_it(name: str) -> None:
    first = (EVIDENCE / name).read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("$ "), f"{name} opens with {first!r}"


def test_both_detectors_are_quiet_when_nothing_has_changed() -> None:
    """Without this every assertion below passes for a detector that always fires."""
    numbers = summary()
    assert numbers["baseline_terraform_plan_exit"] == 0
    assert numbers["baseline_kubectl_diff_exit"] == 0


def test_a_terraform_plan_over_helm_release_cannot_see_a_hand_edit() -> None:
    """The finding. Five replicas running against two declared, and the plan says no changes.

    Asserted rather than described, because it is the sentence somebody would most reasonably
    disbelieve, and because if a future provider version starts detecting this the repository
    should find out from a red build rather than from an interviewer.
    """
    numbers = summary()
    assert numbers["hand_scaled_to"] == 5
    assert numbers["after_hand_edit_terraform_plan_exit"] == 0, (
        "terraform now reports the hand edit. That is better behaviour and it makes this "
        "repository's second detector unnecessary, so rewrite the claim rather than the test"
    )


def test_the_second_detector_does_see_it() -> None:
    """Otherwise the finding above is a complaint rather than a design."""
    numbers = summary()
    assert numbers["after_hand_edit_kubectl_diff_exit"] == 1

    text = (EVIDENCE / "a-hand-edit-and-two-detectors.txt").read_text(encoding="utf-8")
    assert "replicas: 5" in text and "replicas: 2" in text, (
        "the transcript does not show the difference it claims to have found"
    )


def test_the_two_detectors_disagree_which_is_the_whole_point() -> None:
    """Compared rather than asserted separately, so the claim is about the pair.

    One of them being right is not interesting. The two of them disagreeing about the same
    cluster at the same moment is what makes the choice of instrument a decision.
    """
    numbers = summary()
    assert numbers["after_hand_edit_terraform_plan_exit"] == 0
    assert numbers["after_hand_edit_kubectl_diff_exit"] != 0
    assert (
        numbers["after_hand_edit_terraform_plan_exit"]
        != numbers["after_hand_edit_kubectl_diff_exit"]
    )


def test_refresh_false_turns_a_deleted_cluster_into_a_clean_bill_of_health() -> None:
    """The blocker the pre-flight found, kept where it cannot come back.

    With a refresh, a cluster that has been deleted produces a non-zero code, which alarms. With
    `-refresh=false`, the flag everybody adds to make a drift check fast, it exits 0 and prints
    "No changes. Your infrastructure matches the configuration" about infrastructure that does
    not exist.
    """
    numbers = summary()
    assert numbers["unreachable_cluster_plan_exit"] != 0, (
        "a deleted cluster produced exit 0 even with a refresh, so nothing here alarms"
    )
    assert numbers["unreachable_cluster_plan_exit_with_refresh_false"] == 0, (
        "-refresh=false no longer hides a deleted cluster. Check whether the flag is now safe "
        "before relaxing the rule that forbids it"
    )


def test_the_harness_never_uses_refresh_false_for_the_real_check() -> None:
    """Read out of the script, because a rule nobody can verify is a rule that decays.

    The flag appears exactly once, in the step that exists to demonstrate why it is forbidden.
    """
    harness = (REPO / "scripts" / "measure_drift.sh").read_text(encoding="utf-8")

    # INVOCATIONS, not mentions. The first version of this counted every non-comment line
    # containing the flag and found four: the one invocation plus three `echo` lines that print
    # it into the transcript so a reader can see what was run. Counting occurrences of a string
    # in a file that also documents that string is a check on the prose, not on the behaviour.
    invocations = [
        line.strip()
        for line in harness.splitlines()
        if "-refresh=false" in line and line.strip().startswith("terraform ")
    ]
    assert len(invocations) == 1, (
        f"-refresh=false is passed to terraform on {len(invocations)} lines. It belongs only in "
        f"the step that demonstrates why it is forbidden: {invocations}"
    )


def test_every_measured_number_is_present_and_the_count_is_asserted() -> None:
    numbers = summary()
    assert len(numbers) == 7, f"the summary has {len(numbers)} numbers, assert the new one"


def test_no_terraform_working_directory_is_tracked() -> None:
    """121.7 MiB of macOS provider binaries were committed here, and nothing noticed.

    `measure_drift.sh` runs `terraform init` inside the tree, and `.terraform/` was not ignored,
    so a harness run dropped two platform-specific provider binaries into the repository and a
    later `git add -A` committed them. A clone was 41 MB of which 38 were downloadable from the
    registry in a second.

    Asked of git rather than of the filesystem on purpose: the directory is SUPPOSED to be there
    after a run, and the question is whether it is tracked.
    """
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "--", "terraform"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )
    tracked = [line for line in listed.stdout.splitlines() if "/.terraform/" in line]
    assert not tracked, f"terraform's working directory is tracked: {tracked}"


def test_the_provider_lock_covers_both_machines_this_repository_runs_on() -> None:
    """A lock recorded on one machine pins the versions and not the packages for the other.

    `terraform init` records an `h1:` hash for the platform it ran on and nothing else, so a lock
    made on this laptop carried darwin_arm64 alone while CI runs linux_amd64. It worked, because
    the registry's `zh:` hashes cover every platform, which means the gap is invisible until a
    provider is fetched from somewhere that does not supply them.

    Terraform does not label which platform an `h1:` belongs to, so what is asserted is the
    count: two per provider, which is what `terraform providers lock -platform=linux_amd64
    -platform=darwin_arm64` produces and what one `terraform init` does not.
    """
    lock = (REPO / "terraform" / "cluster" / ".terraform.lock.hcl").read_text(encoding="utf-8")
    blocks = lock.split('provider "')[1:]
    assert len(blocks) == 2, f"{len(blocks)} providers in the lock, and the harness needs two"
    for block in blocks:
        name = block.split('"', 1)[0]
        hashes = [line for line in block.splitlines() if '"h1:' in line]
        assert len(hashes) == 2, (
            f"{name} has {len(hashes)} h1 hashes, so the lock covers one platform. Re-record it "
            f"with `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`"
        )


@pytest.mark.cluster
def test_the_drift_findings_still_hold_on_a_cluster() -> None:
    """Needs kind, kubectl, helm and terraform. Creates a cluster, edits it, destroys it."""
    import subprocess

    script = REPO / "scripts" / "measure_drift.sh"
    assert script.exists(), "the harness is missing, so this test proves nothing"

    result = subprocess.run(
        ["bash", str(script), "quayz-drift-pytest"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=1200,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]

    fresh = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    assert fresh["after_hand_edit_terraform_plan_exit"] == 0
    assert fresh["after_hand_edit_kubectl_diff_exit"] == 1
    assert fresh["unreachable_cluster_plan_exit_with_refresh_false"] == 0


def test_the_decision_record_names_what_it_rejected_and_when_argo_cd_wins() -> None:
    """An ADR with no rejected alternative is a description wearing a decision's clothes.

    The Argo CD section is not politeness. "Why not GitOps" is the first question a competent
    reviewer asks about a repository like this one, and answering it in an interview rather than
    in the repository means answering it under pressure and without notes.
    """
    text = (REPO / "docs" / "adr" / "0001-when-a-plan-is-the-wrong-instrument.md").read_text(
        encoding="utf-8"
    )
    assert "Rejected alternatives" in text
    assert "check block" in text.replace("`", ""), (
        "the check-block alternative is not addressed, and it is the one a reader will suggest"
    )
    assert "Argo CD" in text and "Flux" in text
    assert "have not operated Argo CD" in text, (
        "the ADR recommends a tool without saying whether the author has run it, which is the "
        "sentence an interviewer will ask for"
    )
    assert "What this does not establish" in text
    for limit in ("One node", "one namespace", "one release"):
        assert limit in text, f"the ADR does not admit the limit about {limit}"


def test_the_adr_states_the_measurement_rather_than_asserting_the_conclusion() -> None:
    """The exit codes are in it, so a reader can check the claim instead of believing it."""
    text = (REPO / "docs" / "adr" / "0001-when-a-plan-is-the-wrong-instrument.md").read_text(
        encoding="utf-8"
    )
    assert "replicas: 5" in text and "replicas: 2" in text
    assert "-refresh=false" in text
    numbers = summary()
    assert str(numbers["hand_scaled_to"]) in text
