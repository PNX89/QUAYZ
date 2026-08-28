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

AND THE INSTRUMENT TABLE BELOW IS JOINED TO A MEASUREMENT, which it was not. Every entry in
DETECTORS is checked by tests/test_failures.py against docs/evidence/cluster/summary.json, where
the same six instruments are pointed at all five failures and at a healthy control. Three of the
six entries were wrong before that join existed, and all three were wrong in the direction that
flattered this file:

  lastState.terminated.reason was credited with the image pull, which has NO terminated state at
    all, and it was the only entry naming that failure. The test asserting every failure had an
    answer was green because of a false one.
  EndpointSlice readiness was credited with identifying the pod that is alive and never ready. It
    reads zero endpoints ready for every failure here, so what it identifies is a Service that is
    not serving and not which of four reasons.
  pod phase was recorded as separating nothing, with a comment explaining that an empty entry was
    better than a missing one. It is the only instrument that names the image pull on its own:
    Pending against Running for everything else.

The sharpest consequence is one this file could not say before: NO SINGLE FIELD finds the pod
that is alive and never ready. Its row in the matrix differs from a healthy pod in exactly one
column, and that column reads the same for all four failures. It is found by reading every
instrument and finding only one of them abnormal, which is what the controller does when it
falls through every branch.

WHAT IS DELIBERATELY NOT HERE. Nothing about node failure, zone failure, capacity or autoscaling.
A single-node kind cluster cannot demonstrate any of them, and a taxonomy that listed them would
be describing a cluster this repository never runs.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DETECTORS", "FAILURES", "Detector", "Failure", "confusable_with"]


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
        told_apart_by="state.waiting.reason is ImagePullBackOff and the pod phase is Pending. "
        "There is NO container state to read: the container never started, so nothing has "
        "exited and lastState.terminated is empty. Measured, and it is the correction that "
        "matters most here: this failure was credited to lastState.terminated.reason, the one "
        "field it is guaranteed not to appear on",
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
        told_apart_by="nothing positive at all, which is the point: phase Running, restart "
        "count 0, no terminated state, no waiting reason, and zero endpoints ready. It is the "
        "one failure here identified by ELIMINATION rather than by a field, because zero "
        "endpoints ready is also what a crash loop and an OOMKill read",
        why_the_obvious_check_fails="`kubectl get pods` says Running and a restart-count "
        "detector says zero. The deploy looks healthy and serves nothing, which is the only "
        "failure here that a dashboard can show green. And the instrument that finds it, "
        "EndpointSlice readiness, cannot NAME it: it reads zero for every failure in this "
        "taxonomy, so it says the Service is not serving and nothing about why",
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


@dataclass(frozen=True, slots=True)
class Detector:
    """One instrument, and what it was measured to do.

    NOTICING AND SEPARATING ARE DIFFERENT QUESTIONS, and conflating them is how the first version
    of this table came to be wrong in three places. An instrument NOTICES a failure if it reads
    something other than what it reads for a healthy pod. It SEPARATES a failure only if, among
    the ones it notices, that one's reading is its own. EndpointSlice readiness notices four
    failures and separates none of them; it was written down as the answer for one.
    """

    #: The instrument, named precisely enough to query rather than described.
    name: str
    #: The columns of docs/evidence/cluster/summary.json this instrument reads, so that every
    #: entry below is JOINED to the measurement rather than trusted. Empty for the three that
    #: cannot be: two need a cluster edited by hand, which measure_drift.sh produces, and one
    #: reads container logs, which the matrix counts rather than tabulates.
    fields: tuple[str, ...]
    #: Failures where this reads something other than health. Measured, not reasoned about.
    notices: tuple[str, ...]
    #: Of those, the ones this instrument's reading identifies on its own.
    separates: tuple[str, ...]
    #: What it actually read, per case, from docs/evidence/cluster/summary.json.
    measured: str


#: What each instrument can and cannot do, keyed by the instrument rather than by the failure.
#: Written this way round on purpose: the question a person actually has is "I have this tool,
#: what can it not tell me", and answering it needs the tool as the key.
#:
#: EVERY ENTRY IS JOINED TO docs/evidence/cluster/summary.json BY A TEST. Three of the six were
#: wrong before that join existed, and each was wrong in the flattering direction:
#:   lastState.terminated.reason was credited with the image pull, which has NO terminated state
#:   EndpointSlice readiness was credited with telling one failure from the others, and it reads
#:     zero for all four
#:   pod phase was said to separate nothing, and it is the only instrument that finds the image
#:     pull on its own
DETECTORS: tuple[Detector, ...] = (
    Detector(
        name="restart count",
        fields=("restarted",),
        notices=("crash loop", "killed for memory"),
        separates=(),
        measured="restarted for the crash loop and the OOMKill, and not for the other two "
        "failures or for the control. Recorded as a boolean because the COUNT is not a "
        "property of the failure: the same crash loop read 2 here and 3 on a CI runner, "
        "which is how long the harness waited rather than which failure it was, and that "
        "is exactly why a climbing count is not evidence of a kind",
    ),
    Detector(
        name="container logs",
        fields=(),
        notices=("crash loop",),
        separates=("crash loop",),
        measured="one line from the crash loop, the message the process chose on its way out, "
        "against zero lines mentioning a problem from the OOMKill",
    ),
    Detector(
        name="pod phase",
        fields=("phase",),
        notices=("image cannot be pulled",),
        separates=("image cannot be pulled",),
        measured="Pending for the image pull and Running for every other failure and for the "
        "control",
    ),
    Detector(
        name="lastState.terminated.reason with exitCode",
        fields=("terminated_reason", "exit_code"),
        notices=("crash loop", "killed for memory"),
        separates=("crash loop", "killed for memory"),
        measured="Error with 1 against OOMKilled with 137. Absent entirely for the image pull "
        "and for the pod that is alive and never ready, so it finds neither",
    ),
    Detector(
        name="state.waiting.reason",
        fields=("waiting_reason",),
        notices=("crash loop", "killed for memory", "image cannot be pulled"),
        separates=("image cannot be pulled",),
        measured="ImagePullBackOff for the image pull, and CrashLoopBackOff for BOTH of the "
        "other two, which is the same collision one level up",
    ),
    Detector(
        name="EndpointSlice readiness",
        fields=("endpoints_ready",),
        notices=(
            "image cannot be pulled",
            "crash loop",
            "killed for memory",
            "alive but never ready",
        ),
        separates=(),
        measured="zero endpoints ready for every failure, against two for the control. It "
        "answers whether the Service is serving and says nothing about why it is not",
    ),
    Detector(
        name="every instrument at once, read together",
        fields=(
            "phase",
            "restarted",
            "terminated_reason",
            "exit_code",
            "waiting_reason",
            "endpoints_ready",
        ),
        notices=(
            "image cannot be pulled",
            "crash loop",
            "killed for memory",
            "alive but never ready",
        ),
        separates=(
            "image cannot be pulled",
            "crash loop",
            "killed for memory",
            "alive but never ready",
        ),
        measured="every failure's row in the matrix is unique, and this is the ONLY entry here "
        "that separates the pod which is alive and never ready. No single field does: its row "
        "differs from the healthy control in exactly one column, endpoints ready, and that "
        "column reads the same for all four failures. It is found by reading every instrument "
        "and finding only one of them abnormal, which is what the controller does when it falls "
        "through every branch",
    ),
    Detector(
        name="terraform plan over a helm_release",
        fields=(),
        notices=(),
        separates=(),
        measured="exit 0, 'No changes', against a Deployment hand-scaled from two replicas to "
        "five. The resource compares the chart and its values, and neither had changed",
    ),
    Detector(
        name="the declared objects against the live ones",
        fields=(),
        notices=("changed by hand afterwards",),
        separates=("changed by hand afterwards",),
        measured="exit 1 from `helm get values | helm template | kubectl diff` against the same "
        "hand edit the plan above could not see",
    ),
)


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
