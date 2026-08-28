"""The argument of this repository, offline, in about a second.

Run it:

    uv run python examples/tell_them_apart.py

It reads what a real cluster produced, recorded in `docs/evidence/cluster/summary.json` by
`scripts/measure_failures.sh`, and prints the instrument readings side by side. Nothing here
needs kind, Docker or a cluster: the cluster ran when the evidence was captured, and the point
of this file is that you can check the argument without one.

WHAT TO LOOK AT. The `restarts` column is the same for the crash loop and the OOMKill, and so is
`waiting`, and so is `phase`. The `terminated` column is the only one that differs, and the
remedy for one of those failures is not the remedy for the other. That is the whole reason this
repository has a controller rather than a grep.

AND THE LAST ROW IS THE ONE TO ARGUE WITH. The pod that is alive and never ready reads exactly
like a healthy pod on every instrument except one, and that one reads the same for every failure
here. No single field finds it.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from quayz.failures import DETECTORS, FAILURES, confusable_with

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "evidence" / "cluster"

#: The columns, in the order a person would look at them: what you see first, then what actually
#: separates them.
COLUMNS = (
    ("phase", "phase"),
    ("restarted", "restarts"),
    ("waiting_reason", "waiting"),
    ("terminated_reason", "terminated"),
    ("exit_code", "exit"),
    ("endpoints_ready", "ready"),
)


def cases() -> dict[str, dict[str, Any]]:
    loaded: dict[str, Any] = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    found: dict[str, dict[str, Any]] = loaded["cases"]
    return found


def cell(value: object) -> str:
    if value is None:
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def main() -> None:
    measured = cases()
    order = ["healthy", *[failure.name for failure in FAILURES if failure.name in measured]]
    width = max(len(name) for name in order)

    # Widths from the data rather than a guess: CrashLoopBackOff is sixteen characters and a
    # fixed ten pushed every later column out of line, which makes a table that is harder to
    # read than the list it replaced.
    widths = {
        key: max(len(label), *(len(cell(measured[name][key])) for name in order))
        for key, label in COLUMNS
    }

    print("Six instruments, five states, one cluster. Measured, not asserted.\n")
    header = f"{'':<{width}}  " + "  ".join(f"{label:>{widths[key]}}" for key, label in COLUMNS)
    print(header)
    print("-" * len(header))
    for name in order:
        reading = measured[name]
        row = "  ".join(f"{cell(reading[key]):>{widths[key]}}" for key, _ in COLUMNS)
        print(f"{name:<{width}}  {row}")

    crash = measured["crash loop"]
    oom = measured["killed for memory"]
    same = [label for key, label in COLUMNS if crash[key] == oom[key]]
    differ = [label for key, label in COLUMNS if crash[key] != oom[key]]

    print(
        f"\nA crash loop and an OOMKill agree on {len(same)} of these {len(COLUMNS)} columns "
        f"({', '.join(same)})."
    )
    print(f"They differ on {len(differ)}: {', '.join(differ)}.")
    print(
        f"  crash loop        {crash['terminated_reason']} with exit {crash['exit_code']}\n"
        f"  killed for memory {oom['terminated_reason']} with exit {oom['exit_code']}"
    )
    print(f"And they present identically: {confusable_with('crash loop')[0]!r} shares its symptom.")

    print("\nWhat each instrument can do with that:")
    for detector in DETECTORS:
        separates = ", ".join(detector.separates) if detector.separates else "nothing"
        print(f"  {detector.name:<44} separates {separates}")

    never_ready = measured["alive but never ready"]
    healthy = measured["healthy"]
    unlike = [label for key, label in COLUMNS if never_ready[key] != healthy[key]]
    print(
        f"\nThe pod that is alive and never ready differs from a HEALTHY pod in "
        f"{len(unlike)} column ({', '.join(unlike)}),"
    )
    print("and that column reads the same for every failure above. No single field finds it.")


if __name__ == "__main__":
    main()
