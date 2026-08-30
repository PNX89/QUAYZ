"""The published card, joined to the evidence it shows.

WHY THIS FILE EXISTS. site/index.html is the artefact an employer sees first, at
pnx89.github.io/QUAYZ, and it hand-carries two things that are measured elsewhere: the transcript
of a real run, and the numbers about this repository. Both are committed files, so both can go
stale, and a stale number on a public page is the failure this repository is about.

THE CARD TOLD THE READER THE OPPOSITE IN AS MANY WORDS. Under the transcript it says the output
"is committed to the repository and a test fails when it stops matching a live run, so this page
cannot quietly drift from the code it describes", and .github/workflows/pages.yml repeated the
delegation in its header comment. No test in this repository opened the file: a search of tests/
for its name returned nothing at all. That is prose describing an enforcement nobody wrote, which
is the same finding tests/test_capture.py was created for one layer down, so the joins here are
equalities against the capture rather than searches of the page.

WHAT IS DELIBERATELY NOT CHECKED. The card's prose is written by the generator that builds it,
which lives outside this repository, so nothing here pins its wording. What is pinned is every
figure it states and every character it must not render.
"""

from __future__ import annotations

import html
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
CARD = REPO / "site" / "index.html"
EVIDENCE = REPO / "docs" / "evidence"

#: Written as escapes and never as themselves. A file that bans a character and then contains it
#: is a guard that starts failing the moment git tracks it, which this toolset has done three
#: times. The grave accent is here for the same reason: the check below counts them.
EM_DASH = "\u2014"
EN_DASH = "\u2013"
GRAVE_ACCENT = "\u0060"

#: The label on each cell of the facts strip against the key in docs/evidence/facts.json that
#: produced it. Spelled out rather than derived from whatever the page happens to show, because a
#: cell that quietly disappears would otherwise be one comparison fewer and a green suite.
JOINED_CELLS = {"Tests": "tests", "Python": "python", "Release": "release"}


def card() -> str:
    return CARD.read_text(encoding="utf-8")


def facts() -> dict[str, object]:
    loaded: dict[str, object] = json.loads((EVIDENCE / "facts.json").read_text(encoding="utf-8"))
    return loaded


def test_a_published_card_shows_the_captured_demo() -> None:
    """The transcript on the page, against the transcript the capture took.

    tests/test_capture.py re-runs the demo and compares it to docs/evidence/demo.txt, so that
    file is known to be live. This is the other half of the join, and it was the missing half:
    the page the public reads was compared to neither the capture nor the run.
    """
    blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", card(), re.S)
    assert len(blocks) == 1, (
        f"the card holds {len(blocks)} output blocks and this compares exactly one, so the "
        "transcript it shows is not the one being checked"
    )
    shown = html.unescape(blocks[0])
    captured = (EVIDENCE / "demo.txt").read_text(encoding="utf-8")
    assert shown == captured.rstrip("\n"), (
        "the card's transcript is not the captured run, so the page has drifted from the code "
        "it describes. Regenerate both:\n  uv run python scripts/capture_evidence.py"
    )


def test_every_number_on_the_card_is_one_the_capture_measured() -> None:
    """Each cell of the facts strip against the key that produced it.

    Not a search of the page for the number. A document this long contains any digit somewhere,
    and the comparison that matters is cell by cell: a card saying 987 tests and a suite of 139
    is the failure, and it survives any check that only asks whether the page mentions a total.
    """
    strip = re.search(r'<dl class="facts">(.*?)</dl>', card(), re.S)
    assert strip, "the card has no facts strip, so the numbers it states are joined to nothing"
    shown = {
        html.unescape(label).strip(): html.unescape(value).strip()
        for label, value in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", strip.group(1), re.S)
    }
    missing = set(JOINED_CELLS) - set(shown)
    assert missing == set(), (
        f"the card no longer states {sorted(missing)}, so those figures are published by nobody "
        f"and checked by nobody. It shows {sorted(shown)}."
    )

    measured = facts()
    for label, key in JOINED_CELLS.items():
        assert shown[label] == str(measured[key]), (
            f"the card says {label} is {shown[label]!r} and the capture measured "
            f"{str(measured[key])!r}. Regenerate: uv run python scripts/capture_evidence.py"
        )


def test_the_card_renders_no_markup_it_failed_to_convert() -> None:
    """A grave accent on a published page is Markdown that a program moved without looking.

    Two of them sat around the one command name in the claim paragraph, which is the largest text
    on the card and the first thing a reader's eye lands on. They mean nothing in HTML and are
    drawn as characters. The card carries a code element and a rule for it, so there is somewhere
    for that text to go, and this is the check that keeps it there: the claim gets reworded, and
    the wording comes from a generator outside this repository.
    """
    stray = card().count(GRAVE_ACCENT)
    assert stray == 0, (
        f"the card renders {stray} grave accents, which is Markdown pasted into HTML. Wrap the "
        "command name in a code element instead of quoting it."
    )


def test_the_card_carries_no_banned_dash() -> None:
    """The publication guard, run before publication rather than at it.

    .github/workflows/pages.yml refuses to deploy a card containing either dash. That is the
    right place for a last line of defence and the wrong place for the only one, because a red
    deploy is found after main has already moved.
    """
    text = card()
    for name, dash in (("em dash", EM_DASH), ("en dash", EN_DASH)):
        assert dash not in text, (
            f"the card contains an {name}, so Pages will refuse to publish it and the live page "
            "will silently stay on the previous commit"
        )
