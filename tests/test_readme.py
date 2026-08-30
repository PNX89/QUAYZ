"""Every claim the README makes, checked against the repository it describes.

Written before the README, which is the only order that works: a test written afterwards is
written to pass. The first one here failed for a real reason, that the generated block did not
exist yet.

WHEN ONE OF THESE FAILS THE FIRST QUESTION IS WHETHER THE TEST IS WRONG, and the second failure
in this file was exactly that. It looked for `kubectl get pods` in the opening, the opening says
it, and the phrase is wrapped across two lines, so the search failed on where somebody's editor
broke the line rather than on anything the README claimed. Whitespace is normalised now.

It has happened twice more in a sibling repository: a test that banned a phrase rather than a
claim failed against the paragraph explaining why the phrase was wrong, and a test asserting an
exact lock count failed on a platform where the count differed and the claim did not.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
README = REPO / "README.md"
EVIDENCE = REPO / "docs" / "evidence"

sys.path.insert(0, str(REPO / "scripts"))


def readme() -> str:
    return README.read_text(encoding="utf-8")


def section(heading: str) -> str:
    """One section of the README, whitespace normalised.

    Found by its heading rather than by line number, and normalised for the reason recorded in
    the module docstring above: a claim's presence must not depend on where somebody's editor
    broke the line.
    """
    text = readme()
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return " ".join(text[start : end if end != -1 else len(text)].split())


def figures(clause: str, pattern: str, what: str) -> tuple[int, ...]:
    """The numbers one sentence carries, found by the phrase that carries them.

    NOT `str(value) in text`, WHICH IS HOW THE ASSERTION BELOW CAME TO BE SATISFIED BY A BADGE.
    A page long enough to be worth checking contains any single digit somewhere: this README's
    only two 5 characters are a hex colour in a shields.io URL and a Kubernetes version, and
    neither is about replicas, so the check passed while the sentence said fifty.
    """
    found = re.search(pattern, clause)
    assert found, f"the README no longer says {what}. Read from: {clause!r}"
    return tuple(int(group) for group in found.groups())


def cases() -> dict[str, dict[str, object]]:
    loaded = json.loads((EVIDENCE / "cluster" / "summary.json").read_text(encoding="utf-8"))
    found: dict[str, dict[str, object]] = loaded["cases"]
    return found


def test_the_readme_instrument_table_matches_the_declared_limits() -> None:
    """The generated block, regenerated and compared.

    The table makes two kinds of claim at once: what each instrument SEPARATES, declared in
    failures.py, and what it READ, measured on a cluster. Typing either beside the other is how
    that table came to be wrong in three places, so it is generated from both and this is the
    test that keeps the README honest about it.
    """
    from readme_block import block

    text = readme()
    start, end = "<!-- instruments:start -->", "<!-- instruments:end -->"
    assert start in text and end in text, "the README has no generated instrument block"
    committed = text[text.index(start) : text.index(end) + len(end)]
    assert committed == block(), (
        "the README's instrument table is not what failures.py and the evidence say. "
        "Regenerate it:\n  uv run python scripts/readme_block.py --write"
    )


def test_the_instrument_table_is_above_the_fold() -> None:
    """A taxonomy a reader has to scroll for is a taxonomy written for the author's benefit.

    Forty lines is the bar the portfolio checker uses for the headline file, and the same bar
    is applied here to the thing this repository exists to say.
    """
    lines = readme().splitlines()
    position = next(i for i, line in enumerate(lines) if "<!-- instruments:start -->" in line)
    assert position < 40, f"the table starts at line {position + 1}, below the fold"


def test_the_first_screenful_names_the_pair_the_whole_repository_is_about() -> None:
    """Not "Kubernetes deploys" in general. The OOMKill against the crash loop, in the opening."""
    # Whitespace normalised, because the phrase this looks for is wrapped across two lines in
    # the file and a claim's presence must not depend on where the author's editor broke it.
    # This test failed on that and the TEST was what was wrong.
    opening = " ".join("\n".join(readme().splitlines()[:12]).split()).lower()
    assert "crash loop" in opening
    assert "memory" in opening, "the opening does not name the failure it is contrasted with"
    assert "kubectl get pods" in opening, (
        "the opening does not say WHERE they look the same, which is the whole claim"
    )


#: How the opening may write a count: the word, or the digit. A README that opens with a numeral
#: reads like a spec sheet, so the sentence spells the number and this spells it back, which is
#: the whole of the translation needed to compare a claim against a set. Both spellings are
#: accepted, so a rewrite that prefers one is not a red build about typography.
IN_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def test_the_opening_counts_the_failures_that_were_actually_measured() -> None:
    """FIVE STATES ARE NOT FIVE FAILURES, and the opening said they were.

    The matrix holds five cases and one of them is the healthy control, so four failures were
    produced against a cluster and read with six instruments. The fifth entry in the taxonomy, a
    deploy changed by hand afterwards, is produced by a different harness against a healthy
    cluster and is not a pod that stops serving at all: its own row says Running and Ready. The
    opening welded the two counts together and reached the wrong one either way, because reading
    it as the table's five rows counts the control as a failure.

    Recomputed from the evidence and compared against the sentence that carries the claim, so
    the two cannot drift apart again and neither can be fixed without the other going red.
    """
    measured = sorted(set(cases()) - {"healthy"})
    assert measured, "the cluster summary holds nothing but the control, so this compares nothing"

    opening = " ".join("\n".join(readme().splitlines()[:20]).split())
    carrier = re.search(r"(\w+) ways a deploy ends with a pod that is not serving", opening)
    assert carrier, (
        "the opening no longer says how many ways a deploy ends with a pod that is not serving, "
        "which is the claim the whole table is underneath"
    )
    said = carrier.group(1).lower()
    assert said in {IN_WORDS.get(len(measured), str(len(measured))), str(len(measured))}, (
        f"the opening says {said} ways a deploy ends with a pod that is not serving, and the "
        f"cluster produced {len(measured)}: {measured}. The healthy control is not a failure and "
        f"the hand edit is not a pod that stopped serving."
    )


def test_every_command_the_readme_shows_is_one_this_repository_runs() -> None:
    """Every fenced shell command, checked against what is actually here.

    Not by running them: two need a cluster this machine may not have. What is checked is that
    the thing each one invokes exists, which is the failure that actually happens. A README
    telling a reader to run a script that was renamed is the most ordinary way for one to become
    false.
    """
    commands = []
    for fence in re.findall(r"```(?:bash|console|sh)\n(.*?)```", readme(), re.S):
        for raw in fence.splitlines():
            line = raw.strip().removeprefix("$ ").strip()
            if line and not line.startswith("#"):
                commands.append(line)
    assert commands, "the README shows no commands at all"

    for command in commands:
        words = command.split()
        for word in words:
            if word.startswith(("scripts/", "examples/", "charts/", "controller/", "terraform/")):
                assert (REPO / word).exists(), f"{command!r} names {word}, which does not exist"
        if "pytest" in words and "-m" in words:
            marker = words[words.index("-m") + 1].strip("\"'")
            declared = (REPO / "pyproject.toml").read_text(encoding="utf-8")
            assert f'"{marker}:' in declared, f"{command!r} uses marker {marker!r}, undeclared"


def test_the_readme_numbers_are_the_ones_the_cluster_produced() -> None:
    """Every figure the README states about a failure, recomputed from the evidence.

    These are the sentences an interviewer would pick out, and the easiest thing in the file to
    leave behind after a re-measurement. They are read from summary.json here rather than
    trusted, and the exit codes are matched as whole words so that "137" cannot be satisfied by
    a line about something else that happens to contain it.
    """
    text = readme()
    crash = cases()["crash loop"]
    oom = cases()["killed for memory"]
    numbers = json.loads((EVIDENCE / "cluster" / "summary.json").read_text(encoding="utf-8"))

    assert str(oom["terminated_reason"]) in text, "the README does not name the OOMKill's reason"
    assert str(crash["terminated_reason"]) in text, "the README does not name the crash reason"

    # The OOMKill's logs are the claim most worth checking: the README says zero, so zero is
    # what the cluster must have recorded.
    assert numbers["oom_log_lines_mentioning_a_problem"] == 0, (
        "the OOMKilled container logged something about a problem, so the README's central "
        "sentence about log-based detectors is no longer true"
    )
    assert numbers["crash_loop_log_lines"] >= 1

    drift = json.loads((EVIDENCE / "drift" / "summary.json").read_text(encoding="utf-8"))
    assert drift["after_hand_edit_terraform_plan_exit"] == 0, (
        "the README says a plan over helm_release exits 0 on a hand edit, and it no longer does"
    )
    assert drift["after_hand_edit_kubectl_diff_exit"] != 0

    # THE SENTENCE, NOT THE DIGIT. This was `str(drift["hand_scaled_to"]) in text`, which is
    # `"5" in text` over the whole file, and the README's only two 5 characters are a badge's
    # hex colour and a Kubernetes version. The scale is read out of the sentence that makes the
    # claim now, which is also why the README writes it as a digit rather than as a word.
    scaled = figures(
        section("## Drift is a different question"),
        r"hand-scaled from (\d+) replicas to (\d+)",
        "what the Deployment was hand-scaled from and to",
    )
    assert scaled[1] == drift["hand_scaled_to"], (
        f"the drift section says it was scaled to {scaled[1]} and the harness scaled it to "
        f"{drift['hand_scaled_to']}"
    )


def test_the_readme_recovery_numbers_are_the_ones_the_rollback_harness_produced() -> None:
    """The third summary, which nothing here read.

    THE CONTRACT REPORTED ITSELF SATISFIED WHILE A WHOLE SECTION WAS JOINED TO NOTHING. The test
    above reads the cluster and drift summaries; docs/evidence/rollback/summary.json was opened
    by no test at all, so every figure in the Recovering section was prose sitting beside a
    measurement nobody compared it to. The section could say `--atomic` exits 0, which is the
    reversal of the sentence the rollback harness exists to prove, and the suite stayed green.

    Worse than an ordinary drift risk, because scripts/measure_rollback.sh regenerates that file
    on a cluster run: these numbers are EXPECTED to move, and when they moved the README would
    not have.

    Each figure is read from the clause that carries it rather than searched for in the page.
    """
    measured = json.loads((EVIDENCE / "rollback" / "summary.json").read_text(encoding="utf-8"))
    clauses = section("## Recovering").split(";")

    def carrying(command: str) -> str:
        found = [clause for clause in clauses if command in clause]
        assert len(found) == 1, (
            f"{command!r} names {len(found)} clauses of the Recovering section, so there is no "
            "one sentence to compare the figures against"
        )
        return found[0]

    atomic = carrying("--atomic")
    assert figures(atomic, r"exits (\d+)", "what an atomic upgrade exits") == (
        measured["atomic_upgrade_exit_code"],
    )
    assert figures(atomic, r"(\d+) of (\d+) pods ready", "what an atomic upgrade leaves") == (
        measured["atomic_pods_ready_afterwards"],
        measured["atomic_pods_total_afterwards"],
    )

    bare = carrying("a bare")
    assert figures(bare, r"exits (\d+)", "what a bare upgrade exits") == (
        measured["bare_upgrade_exit_code"],
    )
    # The trap itself: ready and total are different numbers here, and a check that only counted
    # ready pods would call this healthy. That is the sentence, so both are compared.
    assert figures(bare, r"(\d+) ready of (\d+) total", "what a bare upgrade leaves") == (
        measured["pods_ready_while_the_broken_revision_stood"],
        measured["pods_total_while_the_broken_revision_stood"],
    )

    rollback = carrying("`helm rollback`")
    assert figures(rollback, r"exits (\d+)", "what a rollback exits") == (
        measured["rollback_exit_code"],
    )
    assert figures(rollback, r"(\d+) of (\d+)", "what a rollback leaves") == (
        measured["pods_ready_after_the_rollback"],
        measured["pods_total_after_the_rollback"],
    )
    assert figures(rollback, r"(\d+) revisions", "how many revisions the history holds") == (
        measured["revisions_in_the_history"],
    )


def test_every_path_and_link_in_the_readme_resolves() -> None:
    """Every repository-relative path, and every link target inside the repository.

    External URLs are not fetched: a test that reaches the network fails for reasons that have
    nothing to do with this repository, and a reviewer running the suite on a train would see a
    red result about somebody else's outage.
    """
    text = readme()
    missing = []

    for path in re.findall(r"\[`([^`\]]+)`\]", text):
        if not (REPO / path).exists():
            missing.append(f"backticked reference [{path}]")

    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if not (REPO / target.split("#")[0]).exists():
            missing.append(f"link target {target}")

    for target in re.findall(r"^\[[^\]]+\]:\s*(\S+)\s*$", text, re.M):
        if target.startswith(("http://", "https://", "#")):
            continue
        if not (REPO / target.split("#")[0]).exists():
            missing.append(f"reference definition {target}")

    assert not missing, "the README points at things that are not here:\n  " + "\n  ".join(missing)


def test_the_readme_admits_what_a_single_node_cluster_cannot_show() -> None:
    """The limits section, checked by content rather than by heading.

    A repository about instruments that mislead is the wrong place to leave the reader guessing
    which claims are out of scope, and these five are the ones a reviewer will reach for.
    """
    text = readme().lower()
    for limit in ("node failure", "zone failure", "capacity", "autoscaling", "service mesh"):
        assert limit in text, f"the README does not say it cannot show {limit}"
    assert "no production cluster" in text, (
        "the README does not say plainly that there is no production cluster behind it"
    )


def test_the_readme_claims_nothing_this_repository_has_not_run() -> None:
    """The must-never-claim list, as a test rather than as a note in a planning document."""
    text = readme().lower()
    forbidden = {
        "eks": "managed Kubernetes",
        "gke": "managed Kubernetes",
        "aks": "managed Kubernetes",
        "argo cd experience": "a tool this repository names and has not operated",
        "in production": "a production cluster",
        "on call": "an operational role",
        "years of kubernetes": "a length of experience",
    }
    for phrase, why in forbidden.items():
        assert phrase not in text, f"the README claims {why} by saying {phrase!r}"
