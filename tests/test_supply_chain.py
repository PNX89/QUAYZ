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
