"""What the build promises about itself, checked without building anything.

THE REPRODUCIBILITY CLAIM IS SCOPED AND THE SCOPE IS THE INTERESTING PART. Measured across six
independent no-cache builds during the pre-flight: the IMAGE MANIFEST digest was identical every
time, with attestations on and off. The attestation-bearing INDEX digest was not, and cannot be:
provenance carries wall-clock times and a random invocationId, and the SBOM generator stamps a
random document UUID and its own creation time. Neither is normalised by SOURCE_DATE_EPOCH.

So this repository claims the manifest digest is reproducible and says plainly that the index
digest is not, by design. Claiming the index digest were stable would be a claim that fails on
the first rebuild.

AND SOURCE_DATE_EPOCH ALONE IS A TRAP. It normalises the image config and the history entries and
does NOT rewrite file mtimes inside the layer tars. Two builds a second apart produced different
layer digests differing in exactly four bytes, all inside tar octal mtime fields. A team setting
only SOURCE_DATE_EPOCH gets a green result whenever two builds land in the same second and a red
one otherwise, which is the worst kind of wrong: intermittent and flattering. The exporter option
`rewrite-timestamp=true` is what actually works, and it requires SOURCE_DATE_EPOCH to be set.

These tests read the Dockerfile and the workflow rather than building, because buildx is not
installed on the machine this was written on and the build that proves the claim runs in CI.
That is stated rather than hidden: see `test_the_reproducibility_check_runs_somewhere`.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = REPO / "controller" / "Dockerfile"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

#: A FROM line, capturing whatever follows the image reference separator.
FROM_LINE = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)


def froms() -> list[str]:
    return FROM_LINE.findall(DOCKERFILE.read_text(encoding="utf-8"))


def test_every_base_image_is_pinned_by_digest() -> None:
    """A tag is a name somebody else can repoint. A digest is the content.

    Each FROM is checked separately rather than the file being searched for "sha256", because a
    file containing one digest and three tags would pass a search and fail a build review.
    """
    lines = froms()
    assert lines, "the Dockerfile has no FROM lines at all"
    unpinned = [line for line in lines if "@sha256:" not in line]
    assert unpinned == [], f"these bases are pinned by tag rather than digest: {unpinned}"


def test_every_digest_has_the_tag_it_came_from_beside_it() -> None:
    """A digest alone tells a reader nothing about what they are looking at.

    A reviewer should not have to resolve a hash to find out whether the base is Go or Debian,
    so each digest carries its tag in a comment on the line above.
    """
    text = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(text):
        if not line.startswith("FROM "):
            continue
        assert index > 0, "a FROM on the first line has nothing above it to name it"
        above = text[index - 1].strip()
        assert above.startswith("#") and len(above) > 3, (
            f"line {index + 1} pins a digest with no tag named above it: {line}"
        )


def test_the_build_strips_what_would_otherwise_differ_between_machines() -> None:
    """-trimpath and an empty buildid, without which two checkouts differ for no good reason."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "-trimpath" in text, (
        "without -trimpath the binary carries the build machine's directory layout, so two "
        "checkouts in different directories produce different bytes"
    )
    assert "-buildid=" in text
    assert "CGO_ENABLED=0" in text


def test_the_reproducibility_check_runs_somewhere() -> None:
    """The claim is only worth making if something re-runs it, and that something is CI.

    buildx is not installed on the machine this repository was written on, so the build that
    proves reproducibility cannot run locally. Rather than committing a transcript nobody can
    regenerate, the check is enforced in CI on every run: build twice, compare the manifest
    digests, fail if they differ.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "SOURCE_DATE_EPOCH" in workflow, "nothing in CI sets SOURCE_DATE_EPOCH"
    assert "rewrite-timestamp=true" in workflow, (
        "CI sets SOURCE_DATE_EPOCH without rewrite-timestamp, which is the configuration that "
        "passes whenever two builds land in the same second and fails otherwise"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "index digest",
        "manifest digest",
    ],
)
def test_the_claim_names_which_digest_it_is_about(phrase: str) -> None:
    """Because "reproducible build" without saying which digest is the overclaim here."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert phrase in workflow.lower(), (
        f"the workflow does not mention the {phrase}, so a reader cannot tell which of the two "
        f"this repository claims is stable"
    )


TOOLBOX = REPO / "toolbox" / "Dockerfile"


def test_the_toolbox_verifies_every_binary_against_a_checksum() -> None:
    """A version is what you asked for. A checksum is what you got.

    Four downloads, four `sha256sum -c`. Counted rather than searched for, because a file with
    one checksum and three bare downloads passes a search and fails a review.
    """
    text = TOOLBOX.read_text(encoding="utf-8")
    downloads = text.count("curl -fsSLo")
    checks = text.count("sha256sum -c -")
    assert downloads == 4, f"{downloads} downloads, expected four"
    assert checks == downloads, (
        f"{downloads} downloads and {checks} checksum verifications: something is fetched on trust"
    )


def test_the_toolbox_declares_its_platform() -> None:
    """Because the first version of it produced an image that lied about its architecture.

    Built on an arm64 host it downloaded amd64 binaries, succeeded, and reported linux/arm64.
    It ran only because the local VM had emulation. The declaration is here and CI asserts the
    result, since the legacy builder ignores the declaration and buildx honours it.
    """
    text = TOOLBOX.read_text(encoding="utf-8")
    froms = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert froms, "no FROM lines"
    undeclared = [line for line in froms if "--platform=" not in line]
    assert undeclared == [], f"these stages do not declare a platform: {undeclared}"

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "the image must admit what it actually contains" in workflow, (
        "nothing in CI asserts the built image's architecture, which is the only place the "
        "declaration can be checked"
    )


def test_the_toolbox_does_not_run_as_root() -> None:
    """It is meant to be handed a Docker socket, which carries the daemon's authority."""
    text = TOOLBOX.read_text(encoding="utf-8")
    assert "USER toolbox" in text
    assert text.rindex("USER toolbox") > text.rindex("apt-get"), (
        "the USER line comes before the last apt-get, so the image still ends as root"
    )


def test_the_toolbox_is_not_pushed_anywhere() -> None:
    """Terraform's CLI is BUSL 1.1 under IBM and redistribution is where that starts to matter.

    Building an image for whoever clones this is inside the Additional Use Grant. Publishing one
    is a question this repository does not need to answer, so the job does not push.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    toolbox_job = workflow[workflow.index("  toolbox:") : workflow.index("  # A real Kubernetes")]
    assert "--push" not in toolbox_job, "the toolbox job pushes the image"
    assert "BUSL" in workflow, "the workflow does not say why it is not pushed"


#: A `uses:` line, split into the action reference and whatever trails it.
USES = re.compile(r"^\s*(?:- )?uses:\s*(\S+)\s*(#.*)?$", re.MULTILINE)
#: The one first-party call: a reusable workflow in an account this repository's author owns.
FIRST_PARTY = "PNX89/.github/"


def uses() -> list[tuple[str, str]]:
    return [(ref, trailing or "") for ref, trailing in USES.findall(WORKFLOW.read_text("utf-8"))]


def test_every_third_party_action_is_pinned_by_commit() -> None:
    """A tag is a pointer its owner can move, and this repository is about what you can prove.

    Twelve of fourteen were floating major tags, which dependabot.yml described as "an exact
    version". Checked per line rather than by searching the file for a hash, because a workflow
    with one pinned action and twelve tags passes a search and fails a review.
    """
    lines = uses()
    assert lines, "the workflow has no `uses:` lines at all"
    unpinned = [
        ref
        for ref, _ in lines
        if not ref.startswith(FIRST_PARTY) and not re.search(r"@[0-9a-f]{40}$", ref)
    ]
    assert unpinned == [], f"these actions are pinned by a movable tag: {unpinned}"


def test_every_pin_names_the_version_it_came_from() -> None:
    """Forty hex characters tell a reviewer nothing about what they are approving."""
    for ref, trailing in uses():
        if ref.startswith(FIRST_PARTY):
            continue
        assert trailing.strip().startswith("#") and len(trailing.strip()) > 2, (
            f"{ref} is pinned with no version named beside it"
        )


def test_the_one_unpinned_call_is_the_first_party_one_and_is_a_tag_not_a_branch() -> None:
    """A reusable workflow in an account this author owns, at an immutable tag.

    Asserted rather than assumed: a pin that becomes a branch is a pin that moves, and this is
    the only line in the file allowed to be anything other than a commit.
    """
    first_party = [ref for ref, _ in uses() if ref.startswith(FIRST_PARTY)]
    assert len(first_party) == 1, f"expected one first-party call, found {first_party}"
    tag = first_party[0].rsplit("@", 1)[1]
    assert re.fullmatch(r"v\d+(\.\d+)*", tag), f"the shared workflow is pinned to {tag!r}"


def test_nothing_in_the_workflow_fetches_latest() -> None:
    """`@latest` is not a version, and two steps used it to fetch a control plane."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    offenders = [line.strip() for line in workflow.splitlines() if "@latest" in line]
    assert offenders == [], f"these lines fetch whatever is newest today: {offenders}"


def test_the_artefact_that_is_signed_is_the_artefact_that_was_compared() -> None:
    """The job proved one digest reproducible and signed a different one.

    The two comparison builds carry no attestations and the pushed build carries both, so the
    digests were never the same object and nothing said so. The workflow now builds the pushed
    configuration as well, pulls the image manifest out of the attested index, and fails if it
    is not the digest the comparison proved.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "the artefact that gets signed must be the artefact that was compared" in workflow
    # BOTH places, counted. Asserting the string appears somewhere passed while one of the two
    # index readings had been replaced by `select(true)`, which takes whichever entry the index
    # happens to list first, and an attestation manifest is an entry.
    separates = workflow.count('select(.platform.architecture != "unknown")')
    assert separates == 2, (
        f"the image manifest is separated from the attestation manifests in {separates} of the "
        f"two places that read an index, so one of them compares whatever is listed first"
    )
    # And the push carries the exporter option, without which what lands is a third set of bytes.
    push = workflow[workflow.index("build with an SBOM and provenance") :]
    push = push[: push.index("- name: and what landed")]
    assert "rewrite-timestamp=true" in push, (
        "the pushed build does not rewrite timestamps, so its layers carry build-time mtimes "
        "and its digest is not the one this job compared"
    )


def test_the_signature_is_verified_against_this_workflow_and_not_merely_this_repository() -> None:
    """The identity regexp matched any workflow in the repository, including one added later."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    identity = re.search(r'--certificate-identity-regexp "([^"]+)"', workflow)
    assert identity, "nothing verifies a certificate identity"
    pattern = identity.group(1)
    assert pattern.endswith("$"), f"{pattern} is unanchored, so a longer identity satisfies it"
    assert "workflows/ci" in pattern, f"{pattern} names no workflow, so any workflow satisfies it"
    assert "refs/heads/main" in pattern, (
        f"{pattern} accepts a signature made from any ref, including a branch in a fork's "
        f"pull request"
    )


def test_the_workflow_does_not_claim_it_needs_no_credential() -> None:
    """It logs in to ghcr.io with GITHUB_TOKEN and publishes to a public transparency log."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "nothing here needs a credential." not in workflow, (
        "the header denies needing a credential while the supply chain job authenticates to "
        "ghcr.io and writes to Sigstore's public Rekor log"
    )
    assert "Rekor" in workflow, (
        "nothing says the signature is published to a public transparency log, which is the "
        "part a reader would want to know is permanent"
    )
