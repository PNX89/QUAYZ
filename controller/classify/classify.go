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
// The one place the difference is written down is lastState.terminated.reason, which is what this
// reads.
package classify

import (
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
	// NeverReady is a container that is running and whose readiness has not passed. Restart
	// count zero, clean logs, and absent from the Service. The only failure a dashboard shows
	// green.
	NeverReady Verdict = "never-ready"
)

// Finding is one container's verdict with the evidence that produced it.
//
// The evidence is carried rather than discarded because a verdict nobody can check is an opinion.
// Reason and ExitCode are exactly what an operator would have looked up by hand.
type Finding struct {
	Pod       string
	Container string
	Verdict   Verdict
	Reason    string
	ExitCode  int32
	Restarts  int32
}

// Container decides one container's verdict from its status.
//
// ORDER MATTERS AND IS NOT ARBITRARY. OOMKilled is tested BEFORE the generic non-zero exit,
// because an OOMKilled container also has a non-zero exit code, 137, and testing the general case
// first would classify every OOMKill as a crash loop. That is precisely the mistake this package
// exists to avoid, so the order is asserted by a test rather than left to the reader.
func Container(pod string, status corev1.ContainerStatus) Finding {
	finding := Finding{Pod: pod, Container: status.Name, Restarts: status.RestartCount}

	// Waiting with no last state at all: nothing has run, so nothing has failed. The image is
	// the usual reason, and the restart count is zero, which is why a restart-count detector
	// reports health here.
	if waiting := status.State.Waiting; waiting != nil {
		finding.Reason = waiting.Reason
		if waiting.Reason == "ImagePullBackOff" || waiting.Reason == "ErrImagePull" {
			finding.Verdict = ImageUnavailable
			return finding
		}
	}

	if last := status.LastTerminationState.Terminated; last != nil {
		finding.Reason = last.Reason
		finding.ExitCode = last.ExitCode
		if last.Reason == "OOMKilled" {
			finding.Verdict = OOMKilled
			return finding
		}
		if last.ExitCode != 0 {
			finding.Verdict = CrashLooping
			return finding
		}
	}

	// Running and not ready, with nothing having terminated. This is the state that looks
	// healthiest and serves nothing.
	if status.State.Running != nil && !status.Ready {
		finding.Verdict = NeverReady
		return finding
	}

	finding.Verdict = Healthy
	return finding
}

// Pod decides a verdict for every container in a pod.
//
// Init containers are included. An init container that is OOMKilled keeps the pod in Init and the
// app containers never start, so a check that only looked at containers would report a pod with
// no failures at all.
func Pod(pod *corev1.Pod) []Finding {
	findings := make([]Finding, 0, len(pod.Status.ContainerStatuses)+len(pod.Status.InitContainerStatuses))
	for _, status := range pod.Status.InitContainerStatuses {
		findings = append(findings, Container(pod.Name, status))
	}
	for _, status := range pod.Status.ContainerStatuses {
		findings = append(findings, Container(pod.Name, status))
	}
	return findings
}

// Interesting reports whether a verdict is worth acting on, so a caller does not decide by
// comparing against Healthy and quietly miss a verdict added later.
func (v Verdict) Interesting() bool {
	switch v {
	case CrashLooping, OOMKilled, ImageUnavailable, NeverReady:
		return true
	case Healthy:
		return false
	}
	return true // an unrecognised verdict is worth a human look rather than silence
}
