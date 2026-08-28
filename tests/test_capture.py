"""The committed evidence, re-derived rather than trusted.

The card at pnx89.github.io/QUAYZ shows a real run's output and four numbers about this
repository. Both are committed files, so both can go stale, and a stale number on a public page
is the failure this repository is about.

THIS FILE EXISTS BECAUSE ITS ABSENCE WAS A FINDING. `scripts/capture_evidence.py` says "the
test file beside this one re-runs the demo and fails when the committed output stops matching",
and in a sibling repository that sentence was written about a file nobody had created: the fifth
time in this portfolio that prose described an enforcement that did not run. So the claim is
made here and checked here, and the demo is genuinely re-executed rather than inspected.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs" / "evidence"


def facts() -> dict[str, object]:
    loaded: dict[str, object] = json.loads((EVIDENCE / "facts.json").read_text(encoding="utf-8"))
    return loaded


def test_the_committed_demo_output_is_what_the_demo_prints_now() -> None:
    """Re-run, byte for byte. The demo is offline, so this costs about a second.

    Byte for byte is safe here and would not be for the cluster transcripts: this output is
    derived entirely from committed JSON, with no pod names, addresses or ages in it. That
    distinction is the whole reason the cluster evidence is compared through summary.json and
    this one is compared directly.
    """
    result = subprocess.run(
        [sys.executable, "examples/tell_them_apart.py"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    committed = (EVIDENCE / "demo.txt").read_text(encoding="utf-8")
    assert result.stdout == committed, (
        "the demo's output has changed and the committed capture has not. Regenerate it:\n"
        "  uv run python scripts/capture_evidence.py"
    )


def test_the_demo_output_carries_the_argument_and_not_only_a_table() -> None:
    """A capture that happened to be empty, or a table with no conclusion, would pass the above.

    So the content is checked as well as the equality: the two failures that collide have to be
    named, and the sentence about no single field finding the never-ready pod has to be in it.
    """
    text = (EVIDENCE / "demo.txt").read_text(encoding="utf-8")
    assert "crash loop" in text and "killed for memory" in text
    assert "OOMKilled" in text and "137" in text
    assert "No single field finds it." in text, (
        "the demo prints the table and stops short of the conclusion it exists to reach"
    )


def test_the_test_total_on_the_card_is_the_offline_total() -> None:
    """The number a reader gets by cloning this and running pytest with nothing installed.

    Counted here the same way the capture counts it, so a card claiming a suite a reader cannot
    run is a red build. The cluster-marked tests are excluded on purpose: three tests that each
    create a kind cluster are not part of what a stranger gets.
    """
    # Counted through the capture script's own helper, deliberately. pytest reports a
    # collection total in two different shapes depending on whether a marker filter is present,
    # and two parsers written a day apart would disagree about a number neither of them is
    # really about. What this checks is the COMMITTED number against a LIVE count.
    sys.path.insert(0, str(REPO / "scripts"))
    from capture_evidence import collected, test_total

    offline = test_total()
    assert collected() > offline, "nothing is deselected, so the offline total is the whole suite"

    assert facts()["tests"] == offline, (
        f"the card says {facts()['tests']} tests and the offline suite collects {offline}. "
        f"Regenerate: uv run python scripts/capture_evidence.py"
    )


def test_the_python_range_on_the_card_is_the_one_ci_actually_tests() -> None:
    """A card claiming support CI does not exercise is a claim about somebody else's machine."""
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = sorted({v for v in re.findall(r'"(3\.\d+)"', workflow)}, key=lambda v: int(v[2:]))
    assert versions, "the CI matrix names no Python versions"
    assert facts()["python"] == f"{versions[0]} to {versions[-1]}"


def test_the_release_on_the_card_is_the_package_version() -> None:
    """The version is the claim. A tag is the check, and a card names one of them."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert version, "pyproject.toml declares no version"
    assert facts()["release"] == f"v{version.group(1)}"


def test_the_capture_names_a_test_file_that_exists() -> None:
    """The finding that produced this file, kept as a check rather than as a memory.

    capture_evidence.py's docstring promises that a test beside it re-runs the demo. That exact
    promise was made in a sibling repository about a file nobody had written.
    """
    text = (REPO / "scripts" / "capture_evidence.py").read_text(encoding="utf-8")
    named = re.findall(r"`?(tests/test_[a-z_]+\.py)`?", text)
    assert named, "the capture script names no test file, so this check has nothing to police"
    for path in set(named):
        assert (REPO / path).exists(), f"capture_evidence.py names {path}, which does not exist"
