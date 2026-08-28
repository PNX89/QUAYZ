"""The ways a deploy goes wrong, as data, and what actually tells them apart.

THE ARGUMENT THIS REPOSITORY IS BUILT ON. Every one of these ends with a pod that is not serving
traffic, and to a reader of `kubectl get pods` several of them look the same. They are not the
same, they are found by different mechanisms, and the expensive mistake is not failing to notice
a bad deploy: it is noticing and reaching for the wrong instrument.

An OOMKill and a crash loop both show `CrashLoopBackOff` and a non-zero restart count. The logs
of an OOMKilled container end mid-sentence with no error in them, because the kernel took the
process away rather than the process failing, so a log-based detector reports nothing at all and
a restart-count detector reports a crash loop. The one place the difference is written down is
`lastState.terminated.reason`, and that is why this repository has a controller rather than a
grep.

A pod that is alive but never ready is different again: it never restarts, its logs are clean,
and `kubectl get pods` shows `Running`. Only the readiness gate keeps it out of the Service, and
the failure mode is a deploy that looks healthy while serving nothing.

WHAT IS DELIBERATELY NOT HERE. Nothing about node failure, zone failure, capacity or autoscaling.
A single-node kind cluster cannot demonstrate any of them, and a taxonomy that listed them would
be describing a cluster this repository never runs.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DETECTORS", "FAILURES", "Failure", "confusable_with"]


@dataclass(frozen=True, slots=True)
class Failure:
    """One way a deploy ends with a pod that is not serving.

    `looks_like` is what a person sees in `kubectl get pods`, which is the whole reason the
    taxonomy is needed: several of these share it.
    """

    name: str
    what_happens: str
    #: The canonical symptom, as a key rather than as prose. Two failures that present the same
    #: way carry the SAME string here, and `confusable_with` derives the pairs from it.
    #:
    #: It was prose to begin with and that was wrong: "CrashLoopBackOff and a climbing restart
    #: count" and "CrashLoopBackOff and a climbing restart count, identical to a crash loop"
    #: are not equal strings, so the function whose entire job is finding the pairs found none.
    #: A derivation over free text is a derivation over how somebody phrased it that morning.
    presents_as: str
    #: The same symptom in a sentence, for a reader rather than for the derivation.
    looks_like: str
    #: The field or event that distinguishes it, named precisely enough to query.
    told_apart_by: str
    #: Why the obvious instrument gets it wrong.
    why_the_obvious_check_fails: str


FAILURES: tuple[Failure, ...] = (
    Failure(
        name="image cannot be pulled",
        what_happens="the tag or digest does not resolve, or the registry refuses the pull",
        presents_as="ImagePullBackOff with restart count zero",
        looks_like="ErrImagePull, then ImagePullBackOff, restart count zero",
        told_apart_by="a Failed event with reason ErrImagePull on the pod, and no container "
        "state at all: the container never started, so there is nothing to have exited",
        why_the_obvious_check_fails="a restart-count detector sees zero and reports health. "
        "Nothing restarted because nothing ever ran",
    ),
    Failure(
        name="crash loop",
        what_happens="the container starts, fails, and is restarted with growing backoff",
        presents_as="CrashLoopBackOff with a climbing restart count",
        looks_like="CrashLoopBackOff and a climbing restart count",
        told_apart_by="lastState.terminated.reason is Error, and exitCode is the code the "
        "process chose",
        why_the_obvious_check_fails="it is the one failure the obvious check gets right, which "
        "is exactly why the next entry is dangerous: it looks identical",
    ),
    Failure(
        name="killed for memory",
        what_happens="the kernel kills the container for exceeding its memory limit",
        presents_as="CrashLoopBackOff with a climbing restart count",
        looks_like="CrashLoopBackOff and a climbing restart count, identical to a crash loop",
        told_apart_by="lastState.terminated.reason is OOMKilled, and exitCode is 137",
        why_the_obvious_check_fails="the logs end mid-sentence with no error in them, because "
        "the process was taken away rather than failing. A log-based detector reports nothing "
        "and a restart-count detector reports a crash loop. Both are wrong about the cause, and "
        "the fix for one is not the fix for the other",
    ),
    Failure(
        name="alive but never ready",
        what_happens="the process runs, and its readiness check never passes",
        presents_as="Running with restart count zero",
        looks_like="Running, restart count zero, and nothing in the logs",
        told_apart_by="the pod is absent from the Service's EndpointSlice, or present with "
        "conditions.ready false",
        why_the_obvious_check_fails="`kubectl get pods` says Running and a restart-count "
        "detector says zero. The deploy looks healthy and serves nothing, which is the only "
        "failure here that a dashboard can show green",
    ),
    Failure(
        name="changed by hand afterwards",
        what_happens="somebody scales or edits the deployed object outside the deploy path",
        presents_as="Running and Ready, nothing wrong at all",
        looks_like="nothing at all: the pods are Running and Ready and the change is intended "
        "by whoever made it",
        told_apart_by="a plan against the declared configuration reports a difference",
        why_the_obvious_check_fails="every health check passes, because the cluster is healthy. "
        "It is the configuration that is no longer what anybody wrote down, and health is the "
        "wrong question",
    ),
)

#: What a detector can and cannot separate, keyed by the instrument rather than by the failure.
#: Written this way round on purpose: the question a person actually has is "I have this tool,
#: what can it not tell me", and answering it needs the tool as the key.
DETECTORS: dict[str, tuple[str, ...]] = {
    "restart count": ("crash loop", "killed for memory"),
    "container logs": ("crash loop",),
    "pod phase": (),
    "lastState.terminated.reason": ("crash loop", "killed for memory", "image cannot be pulled"),
    "EndpointSlice readiness": ("alive but never ready",),
    "a plan against the configuration": ("changed by hand afterwards",),
}


def confusable_with(name: str) -> tuple[str, ...]:
    """The other failures that share this one's `looks_like`.

    Derived from the taxonomy rather than listed separately, so a sixth failure that happens to
    look like an existing one joins the confusable set without anybody remembering to say so.
    """
    subject = next(failure for failure in FAILURES if failure.name == name)
    return tuple(
        failure.name
        for failure in FAILURES
        if failure.name != name and failure.presents_as == subject.presents_as
    )
