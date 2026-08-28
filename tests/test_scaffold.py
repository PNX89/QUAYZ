"""The scaffold, asserted so the first commit is a green build rather than an empty one.

A repository whose first CI run is red teaches its own author to ignore the badge. These are
small on purpose: they check the shape of the thing rather than any behaviour, because there is
no behaviour yet.
"""

from __future__ import annotations

import pathlib
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]


def pyproject() -> dict[str, object]:
    with (REPO / "pyproject.toml").open("rb") as handle:
        data: dict[str, object] = tomllib.load(handle)
    return data


def test_the_package_imports_and_declares_a_version() -> None:
    import quayz

    assert quayz.__version__ == "0.1.0"


def test_every_marker_the_addopts_deselects_is_a_declared_marker() -> None:
    """The two lists are written separately and have to agree.

    A marker deselected by default and never declared is a typo that silently runs the tests it
    was meant to hold back. A marker declared and never deselected runs on a machine that cannot
    support it. Both are quiet failures, so the two are compared rather than trusted.
    """
    # Walked with typed locals rather than chained subscripts on an object. tomllib returns
    # dict[str, Any] and chaining through it needs an ignore on every step, which is four
    # suppressions to read one value.
    tools = pyproject()["tool"]
    assert isinstance(tools, dict)
    pytest_config = tools["pytest"]["ini_options"]
    addopts = str(pytest_config["addopts"])
    declared = {str(entry).split(":")[0] for entry in pytest_config["markers"]}
    deselected = {word for word in addopts.replace("'", " ").split() if word in declared}

    assert declared == deselected, (
        f"declared markers {sorted(declared)} and deselected markers {sorted(deselected)} "
        f"disagree, so a suite is either running where it cannot or hidden where it could"
    )
    assert declared == {"cluster", "envtest", "container"}


def test_no_third_party_binary_is_committed() -> None:
    """envtest fetches kube-apiserver and etcd, and neither belongs in an MIT tree.

    Checked against the tree rather than against .gitignore, because an ignore rule added after
    a file was already tracked does nothing at all.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    forbidden = [
        path
        for path in tracked
        if any(part in {"bin", "envtest-bins", "vendor"} for part in pathlib.Path(path).parts)
    ]
    assert forbidden == [], f"third-party binaries are tracked: {forbidden}"
