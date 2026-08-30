// Package classify decides which failure a container hit, from the pod status alone.
//
// THE WHOLE REASON THIS IS A PACKAGE AND NOT A GREP. A crash loop and a container killed for
// memory are identical in `kubectl get pods`: both show CrashLoopBackOff with a climbing restart
// count. They are also identical in the logs, and worse than identical, because an OOMKilled
// container's logs end mid-sentence with nothing wrong in them. The kernel took the process away
// rather than the process failing.
//
// Measured on a kind cluster and recorded in docs/evidence/cluster: the crash loop reported
// reason Error with exit code 1 and one log line, and the OOMKill reported reason OOMKilled with
// exit code 137 and zero log lines mentioning a problem.
//
// The one place the difference is written down is the terminated state's reason, which is what
// this reads. BOTH terminated states, and that is a correction: the first version read
// lastState.terminated only, so a container that was dead at the moment it was looked at, with
// nothing yet in lastState, came back healthy with its reason and exit code zeroed. A container
// dies before it has died twice.
//
// AND NOT READY IS NOT A FAILURE YET. Running and not ready is also what every pod looks like for
// its first seconds, so classifying it immediately reports an ordinary rolling deploy as broken.
// The verdict needs a clock, so this package takes one rather than pretending the question can be
// answered without it.
package classify

import (
	"time"

	corev1 "k8s.io/api/core/v1"
)

// Verdict is what happened to a container, as a value rather than a string somebody parses.
type Verdict string

const (
	// Healthy covers both a running container and one that has not failed yet. It is not a
	// claim that the workload is correct, only that no failure is visible here.
	Healthy Verdict = "healthy"
	// CrashLooping is a container that exited non-zero and is being restarted.
	CrashLooping Verdict = "crash-looping"
	// OOMKilled is a container the kernel stopped for exceeding its memory limit. It presents
	// exactly as CrashLooping and the remedy is different: more memory, or less allocation, not
	// a fix to whatever the process was doing when it stopped.
	OOMKilled Verdict = "oom-killed"
	// ImageUnavailable is a container that never started, so nothing has exited and the restart
	// count is zero. A restart-count detector calls this healthy.
	ImageUnavailable Verdict = "image-unavailable"
	// NeverReady is a container that has been running longer than ReadyGrace and whose readiness
	// has still not passed. Restart count zero, clean logs, and absent from the Service. The only
	// failure a dashboard shows green.
	NeverReady Verdict = "never-ready"
	// Starting is running and not ready, and not for long enough to be a finding. It exists so
	// that the difference between "starting" and "never ready" is a decision this package makes
	// out loud, rather than a report that happens to be true of both.
	Starting Verdict = "starting"
	// Unclassified is a container waiting for a reason this package does not name. Reported
	// rather than swallowed: an unknown reason with the word healthy beside it is the shape of
	// mistake this repository is about.
	Unclassified Verdict = "unclassified"
)

// ReadyGrace is how long a container may be running and not ready before that is worth reporting.
//
// A NUMBER RATHER THAN AN INSTANT VERDICT, BECAUSE THE INSTANT VERDICT IS WRONG. Every pod is
// Running and not ready between the moment its process starts and the moment its first readiness
// probe passes, so without a grace period an ordinary rolling deploy reports every new pod as the
// failure this repository is most interested in. Sixty seconds is longer than the chart's own
// readiness path (initialDelaySeconds 1, periodSeconds 2) by a wide margin, and short enough that
// a genuinely stuck pod is named within a minute.
const ReadyGrace = 60 * time.Second

// startingReasons are the waiting reasons that mean "not yet", not "wrong". Anything else waiting
// is Unclassified rather than Healthy.
var startingReasons = map[string]bool{
	"ContainerCreating": true,
	"PodInitializing":   true,
	// A container in backoff has already terminated, and the terminated state below carries the
	// evidence. Passing it through here rather than naming it means the reason and the exit code
	// come from the termination rather than from the backoff.
	"CrashLoopBackOff": true,
}

// Subject is the pod a container status belongs to.
//
// BOTH HALVES, BECAUSE A NAME IS NOT AN IDENTITY. A ContainerStatus carries neither, and two pods
// called `web-0` in two namespaces are two pods. A Finding that cannot tell them apart is
// deduplicated down to one report, and the other namespace never hears.
type Subject struct {
	Namespace string
	Name      string
}

// Finding is one container's verdict with the evidence that produced it.
//
// The evidence is carried rather than discarded because a verdict nobody can check is an opinion.
// Reason and ExitCode are exactly what an operator would have looked up by hand.
type Finding struct {
	Namespace string
	Pod       string
	Container string
	Verdict   Verdict
	Reason    string
	ExitCode  int32
	Restarts  int32
}

// Container decides one container's verdict from its status, as of now.
//
// ORDER MATTERS AND IS NOT ARBITRARY. OOMKilled is tested BEFORE the generic non-zero exit,
// because an OOMKilled container also has a non-zero exit code, 137, and testing the general case
// first would classify every OOMKill as a crash loop. That is precisely the mistake this package
// exists to avoid, so the order is asserted by a test rather than left to the reader.
//
// THE CURRENT TERMINATED STATE IS READ BEFORE THE PREVIOUS ONE, for the same reason: a container
// that is dead right now is a stronger fact about it than what happened last time round.
func Container(subject Subject, status corev1.ContainerStatus, now time.Time) Finding {
	finding := Finding{
		Namespace: subject.Namespace,
		Pod:       subject.Name,
		Container: status.Name,
		Restarts:  status.RestartCount,
	}

	// Waiting with nothing having run: the image is the usual reason, and the restart count is
	// zero, which is why a restart-count detector reports health here.
	if waiting := status.State.Waiting; waiting != nil {
		finding.Reason = waiting.Reason
		switch {
		case waiting.Reason == "ImagePullBackOff" || waiting.Reason == "ErrImagePull":
			finding.Verdict = ImageUnavailable
			return finding
		case startingReasons[waiting.Reason]:
			// Fall through to the terminated states, which carry the real evidence.
		default:
			// A reason nobody here has named. Saying healthy while carrying it in the Reason
			// field is the worst of both, so it is said out loud instead.
			finding.Verdict = Unclassified
			return finding
		}
	}

	// Dead now, and dead before. Read in that order.
	for _, terminated := range []*corev1.ContainerStateTerminated{
		status.State.Terminated,
		status.LastTerminationState.Terminated,
	} {
		if terminated == nil {
			continue
		}
		// A CLEAN EXIT IN THE PREVIOUS SLOT DECIDES NOTHING, and reading it as though it did
		// returned the one word this package exists not to say. Every container carries a clean
		// LastTerminationState after any restart whose process exited zero: a server that traps
		// SIGTERM and shuts down tidily after a liveness restart, a shell entrypoint that
		// finished, anything that called exit(0). Returning here skipped the running-and-not-ready
		// check below, so a container running and unready for hours came back healthy, and
		// Healthy is not Interesting, so watch.Run reported it to nobody. A clean exit is
		// evidence that nothing failed, not evidence about whether the container is serving now.
		clean := terminated.Reason != "OOMKilled" && terminated.ExitCode == 0
		current := terminated == status.State.Terminated
		if clean && !current {
			break
		}
		finding.Reason = terminated.Reason
		finding.ExitCode = terminated.ExitCode
		if terminated.Reason == "OOMKilled" {
			finding.Verdict = OOMKilled
			return finding
		}
		if terminated.ExitCode != 0 {
			finding.Verdict = CrashLooping
			return finding
		}
		// Exited zero and terminated right now. A completed container is not a failure, and
		// looking at the previous termination after a clean current one would report a pod that
		// has since recovered.
		finding.Verdict = Healthy
		return finding
	}

	// Running and not ready. This is the state that looks healthiest and serves nothing, and it
	// is also what every pod looks like while it starts, so the clock decides which one it is.
	if status.State.Running != nil && !status.Ready {
		started := status.State.Running.StartedAt.Time
		if started.IsZero() || now.Sub(started) < ReadyGrace {
			finding.Verdict = Starting
			return finding
		}
		finding.Verdict = NeverReady
		return finding
	}

	finding.Verdict = Healthy
	return finding
}

// Pod decides a verdict for every container in a pod, as of now.
//
// Init containers are included. An init container that is OOMKilled keeps the pod in Init and the
// app containers never start, so a check that only looked at containers would report a pod with
// no failures at all.
func Pod(pod *corev1.Pod, now time.Time) []Finding {
	subject := Subject{Namespace: pod.Namespace, Name: pod.Name}
	findings := make([]Finding, 0, len(pod.Status.ContainerStatuses)+len(pod.Status.InitContainerStatuses))
	for _, status := range pod.Status.InitContainerStatuses {
		findings = append(findings, Container(subject, status, now))
	}
	for _, status := range pod.Status.ContainerStatuses {
		findings = append(findings, Container(subject, status, now))
	}
	return findings
}

// Interesting reports whether a verdict is worth acting on, so a caller does not decide by
// comparing against Healthy and quietly miss a verdict added later.
func (v Verdict) Interesting() bool {
	switch v {
	case CrashLooping, OOMKilled, ImageUnavailable, NeverReady, Unclassified:
		return true
	case Healthy, Starting:
		return false
	}
	return true // an unrecognised verdict is worth a human look rather than silence
}
