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
import urllib.parse

import pytest

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


def test_the_demo_output_carries_the_argument_and_not_only_a_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A capture that happened to be empty, or a table with no conclusion, would pass the above.

    So the content is checked as well as the equality: the two failures that collide have to be
    named, and the sentence about no single field finding the never-ready pod has to be in it.

    ALSO GUARDS THE REASON A CAPTURE CANNOT SILENTLY BE EMPTY: `capture_evidence.main()` writes
    demo.txt from a demo run before it writes facts.json, and nothing here had ever driven it
    with a demo stubbed to print nothing. Pointed at tmp_path rather than the real docs/evidence,
    deliberately: asserting this against the committed demo.txt would itself write the empty
    file there the moment the guard went missing, destroying the evidence this test exists to
    protect in the act of proving the protection is gone.
    """
    text = (EVIDENCE / "demo.txt").read_text(encoding="utf-8")
    assert "crash loop" in text and "killed for memory" in text
    assert "OOMKilled" in text and "137" in text
    assert "No single field finds it." in text, (
        "the demo prints the table and stops short of the conclusion it exists to reach"
    )

    sys.path.insert(0, str(REPO / "scripts"))
    import capture_evidence

    monkeypatch.setattr(capture_evidence, "EVIDENCE", tmp_path)
    monkeypatch.setattr(capture_evidence, "run", lambda *a, **k: "")
    try:
        capture_evidence.main()
    except SystemExit as exit_:
        assert "refusing to write empty evidence" in str(exit_)
    else:
        raise AssertionError("main() did not refuse a demo that produced no output")
    assert not (tmp_path / "demo.txt").exists(), (
        "main() wrote evidence to disk on the path meant to refuse it"
    )


def test_the_test_total_on_the_card_is_the_offline_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    import capture_evidence
    from capture_evidence import collected, test_total

    offline = test_total()
    assert collected() > offline, "nothing is deselected, so the offline total is the whole suite"

    assert facts()["tests"] == offline, (
        f"the card says {facts()['tests']} tests and the offline suite collects {offline}. "
        f"Regenerate: uv run python scripts/capture_evidence.py"
    )

    # THE REFUSAL THIS DIVISION NEEDS: a `-m cluster` marker that has stopped selecting anything
    # makes the cluster-marked slice zero, and nothing distinguishes that from a repository with
    # no cluster tests. Nobody had ever driven test_total() with a marker returning nothing.
    def fake_collected(*extra: str) -> int:
        return 0 if extra == ("-m", "cluster") else 139

    monkeypatch.setattr(capture_evidence, "collected", fake_collected)
    try:
        capture_evidence.test_total()
    except SystemExit as exit_:
        assert "no cluster-marked tests found" in str(exit_)
    else:
        raise AssertionError("test_total() did not refuse an empty cluster-marked count")


def _badge_version(readme: str, label: str) -> str:
    """The version half of one shields.io badge on the README, decoded.

    A shields.io badge URL is `badge/<label>-<message>-<colour>`, joined by hyphens with the
    colour last. The version itself contains no hyphen for any of the four checked here, so
    splitting on the LAST one is enough to drop the colour and keep everything before it,
    including the `%20%7C%20` a multi-value message like the Python range is url-encoded with.
    """
    found = re.search(rf"https://img\.shields\.io/badge/{label}-([^)]+)\)", readme)
    assert found, f"the README has no {label!r} version badge in the shape this test expects"
    message, _, _colour = found.group(1).rpartition("-")
    assert message, f"the {label!r} badge has no version half, only a colour: {found.group(1)!r}"
    return urllib.parse.unquote(message)


def test_the_python_range_on_the_card_is_the_one_ci_actually_tests() -> None:
    """A card claiming support CI does not exercise is a claim about somebody else's machine.

    ALSO JOINS THE FOUR VERSION BADGES ON THE README to the files this repository actually pins:
    controller/go.mod, toolbox/Dockerfile's ARGs, and this same CI matrix. No test read a badge
    before this one, and mutation proved it: rewriting all four to claim Python 2.7 to 3.6, Go
    1.4, kind v9.9.9 and Helm v2.0.0 left the whole suite green.
    """
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    versions = sorted({v for v in re.findall(r'"(3\.\d+)"', workflow)}, key=lambda v: int(v[2:]))
    assert versions, "the CI matrix names no Python versions"
    assert facts()["python"] == f"{versions[0]} to {versions[-1]}"

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert _badge_version(readme, "python") == " | ".join(versions), (
        f"the Python badge says {_badge_version(readme, 'python')!r} and the CI matrix tests "
        f"{versions}"
    )

    go_mod = (REPO / "controller" / "go.mod").read_text(encoding="utf-8")
    go_declared = re.search(r"^go (\d+\.\d+)\.\d+$", go_mod, re.MULTILINE)
    assert go_declared, "controller/go.mod names no go version in MAJOR.MINOR.PATCH form"
    assert _badge_version(readme, "go") == go_declared.group(1), (
        f"the Go badge says {_badge_version(readme, 'go')!r} and controller/go.mod says "
        f"{go_declared.group(1)!r}"
    )

    dockerfile = (REPO / "toolbox" / "Dockerfile").read_text(encoding="utf-8")
    for label, arg in (("kind", "KIND_VERSION"), ("helm", "HELM_VERSION")):
        pinned = re.search(rf"^ARG {arg}=(\S+)$", dockerfile, re.MULTILINE)
        assert pinned, f"toolbox/Dockerfile no longer pins {arg}"
        assert _badge_version(readme, label) == pinned.group(1), (
            f"the {label} badge says {_badge_version(readme, label)!r} and toolbox/Dockerfile "
            f"pins {arg}={pinned.group(1)!r}"
        )


def test_the_release_on_the_card_is_the_package_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The version is the claim. A tag is the check, and a card names one of them."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert version, "pyproject.toml declares no version"
    assert facts()["release"] == f"v{version.group(1)}"

    # THE REFUSAL THIS CROSS-CHECK NEEDS: a version bumped without a matching tag must stop the
    # capture rather than publish the old one, and nothing here had ever driven release() with a
    # tag that disagrees. Patched on our OWN `subprocess` import, not on
    # `capture_evidence.subprocess`: it is the same module object either way, and mypy's strict
    # reexport check refuses the latter as an attribute capture_evidence.py never declared it
    # exports.
    sys.path.insert(0, str(REPO / "scripts"))
    import capture_evidence

    class StubDescribe:
        returncode = 0
        stdout = "v9.9.9\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: StubDescribe())
    try:
        capture_evidence.release()
    except SystemExit as exit_:
        assert "v9.9.9" in str(exit_)
    else:
        raise AssertionError("release() did not refuse a tag that disagrees with the version")


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
