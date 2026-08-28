package classify

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
)

func terminated(reason string, exitCode int32, restarts int32) corev1.ContainerStatus {
	return corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: restarts,
		State:        corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
		LastTerminationState: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: reason, ExitCode: exitCode},
		},
	}
}

// The argument of the whole repository, as one test. These two statuses differ in exactly one
// field, and everything an operator sees first is identical: CrashLoopBackOff, three restarts.
func TestACrashLoopAndAnOOMKillAreSeparatedByReasonAlone(t *testing.T) {
	crash := Container("pod", terminated("Error", 1, 3))
	oom := Container("pod", terminated("OOMKilled", 137, 3))

	if crash.Verdict != CrashLooping {
		t.Fatalf("crash loop classified as %q", crash.Verdict)
	}
	if oom.Verdict != OOMKilled {
		t.Fatalf("OOMKill classified as %q, which is the mistake this package exists to avoid", oom.Verdict)
	}
	if crash.Restarts != oom.Restarts {
		t.Fatal("the fixtures differ in restart count, so this proves less than it claims")
	}
}

// ORDER, asserted rather than left to the reader. An OOMKilled container also has a non-zero
// exit code, so testing the general case first classifies every OOMKill as a crash loop. This
// test fails if the two branches are ever swapped.
func TestAnOOMKillIsNotReadAsAnOrdinaryNonZeroExit(t *testing.T) {
	oom := Container("pod", terminated("OOMKilled", 137, 1))
	if oom.Verdict != OOMKilled {
		t.Fatalf("got %q: the non-zero exit branch is being reached before the OOMKilled one", oom.Verdict)
	}
	if oom.ExitCode != 137 {
		t.Fatalf("exit code %d, and 137 is half the evidence a reader needs", oom.ExitCode)
	}
}

func TestAnImageThatNeverPulledHasNotRestarted(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 0,
		State:        corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "ImagePullBackOff"}},
	}
	finding := Container("pod", status)
	if finding.Verdict != ImageUnavailable {
		t.Fatalf("got %q", finding.Verdict)
	}
	if finding.Restarts != 0 {
		t.Fatal("the fixture has restarts, which is not what this failure looks like")
	}
}

func TestRunningAndNotReadyIsItsOwnVerdict(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:  "canary",
		Ready: false,
		State: corev1.ContainerState{Running: &corev1.ContainerStateRunning{}},
	}
	if got := Container("pod", status).Verdict; got != NeverReady {
		t.Fatalf("got %q, and this is the only failure a dashboard shows green", got)
	}
}

func TestRunningAndReadyIsHealthy(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:  "canary",
		Ready: true,
		State: corev1.ContainerState{Running: &corev1.ContainerStateRunning{}},
	}
	if got := Container("pod", status).Verdict; got != Healthy {
		t.Fatalf("got %q for a container that is running and ready", got)
	}
}

// An init container that dies keeps the pod in Init and the app containers never start, so a
// check reading only ContainerStatuses reports a pod with no failures at all.
func TestAnInitContainerFailureIsNotInvisible(t *testing.T) {
	pod := &corev1.Pod{}
	pod.Name = "pod"
	pod.Status.InitContainerStatuses = []corev1.ContainerStatus{terminated("OOMKilled", 137, 2)}

	findings := Pod(pod)
	if len(findings) != 1 {
		t.Fatalf("got %d findings, so the init container was skipped", len(findings))
	}
	if findings[0].Verdict != OOMKilled {
		t.Fatalf("got %q", findings[0].Verdict)
	}
}

func TestOnlyHealthyIsUninteresting(t *testing.T) {
	for _, verdict := range []Verdict{CrashLooping, OOMKilled, ImageUnavailable, NeverReady} {
		if !verdict.Interesting() {
			t.Fatalf("%q is not interesting, so nothing would act on it", verdict)
		}
	}
	if Healthy.Interesting() {
		t.Fatal("healthy is interesting, so everything would be reported")
	}
	// A verdict nobody has taught it about is worth a human look rather than silence.
	if !Verdict("something-new").Interesting() {
		t.Fatal("an unrecognised verdict is silently uninteresting, which is how a new failure is missed")
	}
}
