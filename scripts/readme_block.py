#!/usr/bin/env python3
"""Generate the README's instrument table from failures.py and the cluster evidence.

    uv run python scripts/readme_block.py --check    exit 1 if README.md disagrees
    uv run python scripts/readme_block.py --write    rewrite the block in place

WHY THIS EXISTS, AND WHY IT READS TWO SOURCES. The table on the first screenful makes two kinds
of claim: what each instrument can separate, which is declared in `src/quayz/failures.py`, and
what each instrument actually read, which is in `docs/evidence/cluster/summary.json` and came
off a real cluster. Typing either beside the other and trusting them to stay equal is how the
detector table came to be wrong in three places at once, all in the direction that flattered it.

So the table is generated from both, and it cannot be written by hand into agreement: the
`separates` column comes from the declaration and the reading columns come from the measurement,
and tests/test_failures.py already fails if the declaration disagrees with the measurement.

Only the block between the markers is generated. The prose around it is written, because a
README entirely produced by a program reads like one.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quayz.failures import DETECTORS, FAILURES  # noqa: E402

README = ROOT / "README.md"
EVIDENCE = ROOT / "docs" / "evidence" / "cluster" / "summary.json"
START = "<!-- instruments:start -->"
END = "<!-- instruments:end -->"

#: The columns of the reading table, in the order a person meets them: what `kubectl get pods`
#: shows first, then the field that actually decides.
COLUMNS = (
    ("phase", "phase"),
    ("restarted", "restarted"),
    ("waiting_reason", "waiting reason"),
    ("terminated_reason", "terminated reason"),
    ("exit_code", "exit"),
    ("endpoints_ready", "endpoints ready"),
)


def cases() -> dict[str, dict[str, object]]:
    loaded = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    found: dict[str, dict[str, object]] = loaded["cases"]
    return found


def cell(value: object) -> str:
    """One table cell. `None` is printed as a word, because an empty cell reads as an oversight.

    It is not an oversight: an absent terminated state IS the finding for the image that cannot
    be pulled, and a blank there would hide the one thing that entry is about.
    """
    if value is None:
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value).replace("|", "\\|")


def readings() -> list[str]:
    """Every instrument pointed at every state, which is the argument in one table."""
    measured = cases()
    order = ["healthy", *[failure.name for failure in FAILURES if failure.name in measured]]
    lines = [
        "| state | " + " | ".join(label for _, label in COLUMNS) + " |",
        "| --- | " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    for name in order:
        reading = measured[name]
        row = " | ".join(cell(reading[key]) for key, _ in COLUMNS)
        label = "**healthy, the control**" if name == "healthy" else name
        lines.append(f"| {label} | {row} |")
    return lines


def first_sentence(text: str) -> str:
    """The reading itself, without the paragraph explaining it.

    Every `measured` field opens with a complete statement of what the instrument read and then
    explains why that matters. The table takes the statement; a first screenful with
    four-hundred-character cells is a first screenful nobody reads, and the rest is one click
    away in `src/quayz/failures.py`.

    Truncation at a width was the alternative and was rejected: a sentence cut mid-clause can
    say something its author did not, which is a strange risk to run in a table about instruments
    that mislead.
    """
    flat = " ".join(text.split())
    first, separator, _ = flat.partition(". ")
    return first + "." if separator else first


def instruments() -> list[str]:
    """What each instrument can and cannot do, with the reading that decided it."""
    lines = [
        "| instrument | separates | what it read |",
        "| --- | --- | --- |",
    ]
    for detector in DETECTORS:
        separates = ", ".join(detector.separates) if detector.separates else "**nothing**"
        measured = first_sentence(detector.measured).replace("|", "\\|")
        lines.append(f"| `{detector.name}` | {separates} | {measured} |")
    return lines


def block() -> str:
    return "\n".join(
        [
            START,
            "",
            *readings(),
            "",
            textwrap.fill(
                "Two of those rows are the reason this repository exists. A crash loop and a "
                "container the kernel killed for memory agree on four of the six columns, and "
                "the two they differ on are the two nobody looks at first.",
                width=96,
            ),
            "",
            *instruments(),
            "",
            textwrap.fill(
                "Generated from `src/quayz/failures.py` and "
                "`docs/evidence/cluster/summary.json` by `scripts/readme_block.py`. The left "
                "column is declared in code and the right one was read off a cluster, and a "
                "test fails if they disagree: three entries in that table were wrong when they "
                "were finally joined to the measurement, every one of them in the direction "
                "that flattered it.",
                width=96,
            ),
            "",
            END,
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"README.md has no {START} / {END} block", file=sys.stderr)
        return 1
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    rebuilt = head + block() + tail

    if args.write:
        README.write_text(rebuilt, encoding="utf-8")
        print("README.md instrument block rewritten")
        return 0

    if rebuilt != text:
        print(
            "README.md's instrument table is not what failures.py and the evidence say. "
            "Regenerate it:\n  uv run python scripts/readme_block.py --write",
            file=sys.stderr,
        )
        return 1
    print("README.md instrument table matches failures.py and the measured evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
