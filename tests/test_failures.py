"""The taxonomy, checked for the property that makes it worth having.

A list of ways a deploy can fail is easy to write and proves nothing. The claim here is sharper:
that some of them are indistinguishable by the instrument people reach for first, and that the
one field which separates them is named. These tests check that claim rather than the list.
"""

from __future__ import annotations

import pytest

from quayz.failures import DETECTORS, FAILURES, Failure, confusable_with


def by_name(name: str) -> Failure:
    return next(failure for failure in FAILURES if failure.name == name)


def test_every_failure_is_here_and_the_count_is_asserted() -> None:
    """Five, and the count is asserted so a sixth has to be added on purpose.

    The set is spelled out rather than counted alone, because a count goes on passing when one
    entry is swapped for another.
    """
    assert {failure.name for failure in FAILURES} == {
        "image cannot be pulled",
        "crash loop",
        "killed for memory",
        "alive but never ready",
        "changed by hand afterwards",
    }
    assert len(FAILURES) == 5


def test_a_crash_loop_and_an_oomkill_are_indistinguishable_by_symptom() -> None:
    """The argument. If these two ever stop colliding, this repository has less to say.

    They share `presents_as` exactly, which is the point: CrashLoopBackOff and a climbing
    restart count is what both produce, and it is what a person sees first.
    """
    assert by_name("crash loop").presents_as == by_name("killed for memory").presents_as
    assert confusable_with("crash loop") == ("killed for memory",)
    assert confusable_with("killed for memory") == ("crash loop",)


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


def test_the_instrument_people_reach_for_first_cannot_separate_the_pair() -> None:
    """Restart count sees both and tells you nothing about which.

    This is the sentence the controller in this repository exists to answer, so it is asserted
    rather than left in prose: the detector covers both members of a confusable pair, which
    means it FIRES on both and SEPARATES neither.
    """
    covered = DETECTORS["restart count"]
    assert "crash loop" in covered and "killed for memory" in covered
    assert set(covered) == set(confusable_with("crash loop")) | {"crash loop"}


def test_one_named_field_does_separate_them() -> None:
    """And it is a field, not a heuristic, so a test can read it off a live pod."""
    reason = DETECTORS["lastState.terminated.reason"]
    assert "crash loop" in reason and "killed for memory" in reason

    oom = by_name("killed for memory")
    assert "OOMKilled" in oom.told_apart_by
    assert "137" in oom.told_apart_by, (
        "the exit code is the second half of the evidence and a reader should not have to know it"
    )


def test_logs_cannot_see_an_oomkill_at_all() -> None:
    """The trap that makes a log-based detector worse than useless here.

    The kernel takes the process away, so the logs end mid-sentence with nothing wrong in them.
    A detector reading logs reports health for a container that was killed.
    """
    assert "killed for memory" not in DETECTORS["container logs"]
    assert "mid-sentence" in by_name("killed for memory").why_the_obvious_check_fails


def test_the_only_failure_a_dashboard_shows_green_is_named_as_such() -> None:
    """Alive but never ready is the one that passes every health check and serves nothing."""
    never_ready = by_name("alive but never ready")
    assert never_ready.presents_as == "Running with restart count zero"
    assert "EndpointSlice" in never_ready.told_apart_by
    assert "green" in never_ready.why_the_obvious_check_fails


def test_pod_phase_separates_nothing_and_says_so() -> None:
    """An empty entry rather than a missing one, because absent reads as "not considered"."""
    assert DETECTORS["pod phase"] == ()


def test_every_detector_names_failures_that_exist() -> None:
    """A detector claiming to catch something not in the taxonomy is a stale entry."""
    names = {failure.name for failure in FAILURES}
    for detector, caught in DETECTORS.items():
        unknown = set(caught) - names
        assert unknown == set(), f"{detector} claims to catch {unknown}, which is not a failure"


def test_every_failure_is_caught_by_at_least_one_detector() -> None:
    """Otherwise the taxonomy names a problem with no answer, which is a gap rather than a list."""
    caught = {name for names in DETECTORS.values() for name in names}
    missing = {failure.name for failure in FAILURES} - caught
    assert missing == set(), f"no detector here catches {missing}"


@pytest.mark.parametrize("failure", FAILURES, ids=lambda f: f.name)
def test_every_entry_says_why_the_obvious_check_fails(failure: Failure) -> None:
    """The field that makes this a taxonomy rather than a list of things that can go wrong."""
    assert len(failure.why_the_obvious_check_fails) > 60, failure.name
    assert len(failure.told_apart_by) > 30, failure.name
    assert failure.presents_as and failure.looks_like
