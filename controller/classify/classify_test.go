package classify

import (
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// EVERY ASSERTION HERE READS THE EVIDENCE AND NOT ONLY THE VERDICT, which is a correction. An
// adversarial pass mutated Finding.Reason and Finding.Restarts so they were never populated at
// all, and the whole suite stayed green: nothing asserted them. A classifier whose verdict is
// right and whose evidence is empty is one an operator cannot check, and checking is the point.

var now = time.Date(2026, 8, 28, 12, 0, 0, 0, time.UTC)

func subject() Subject { return Subject{Namespace: "production", Name: "pod"} }

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

func running(startedAgo time.Duration, ready bool) corev1.ContainerStatus {
	return corev1.ContainerStatus{
		Name:  "canary",
		Ready: ready,
		State: corev1.ContainerState{
			Running: &corev1.ContainerStateRunning{
				StartedAt: metav1.NewTime(now.Add(-startedAgo)),
			},
		},
	}
}

// The argument of the whole repository, as one test. These two statuses differ in exactly one
// field, and everything an operator sees first is identical: CrashLoopBackOff, three restarts.
func TestACrashLoopAndAnOOMKillAreSeparatedByReasonAlone(t *testing.T) {
	crash := Container(subject(), terminated("Error", 1, 3), now)
	oom := Container(subject(), terminated("OOMKilled", 137, 3), now)

	if crash.Verdict != CrashLooping {
		t.Fatalf("crash loop classified as %q", crash.Verdict)
	}
	if oom.Verdict != OOMKilled {
		t.Fatalf("OOMKill classified as %q, which is the mistake this package exists to avoid", oom.Verdict)
	}
	if crash.Restarts != 3 || oom.Restarts != 3 {
		t.Fatalf("restart counts %d and %d, and the fixtures both say 3", crash.Restarts, oom.Restarts)
	}
	if crash.Reason != "Error" || oom.Reason != "OOMKilled" {
		t.Fatalf("reasons %q and %q: the evidence is what makes the verdict checkable", crash.Reason, oom.Reason)
	}
	if crash.ExitCode != 1 || oom.ExitCode != 137 {
		t.Fatalf("exit codes %d and %d", crash.ExitCode, oom.ExitCode)
	}
}

// ORDER, asserted rather than left to the reader. An OOMKilled container also has a non-zero
// exit code, so testing the general case first classifies every OOMKill as a crash loop. This
// test fails if the two branches are ever swapped.
func TestAnOOMKillIsNotReadAsAnOrdinaryNonZeroExit(t *testing.T) {
	oom := Container(subject(), terminated("OOMKilled", 137, 1), now)
	if oom.Verdict != OOMKilled {
		t.Fatalf("got %q: the non-zero exit branch is being reached before the OOMKilled one", oom.Verdict)
	}
	if oom.ExitCode != 137 {
		t.Fatalf("exit code %d, and 137 is half the evidence a reader needs", oom.ExitCode)
	}
}

// A CONTAINER DIES BEFORE IT HAS DIED TWICE. Read only lastState and the first death is
// invisible: this status is what a pod looks like in the window between the process exiting and
// the kubelet restarting it, and it used to come back healthy with reason "" and exit code 0.
func TestAContainerThatIsDeadRightNowIsNotHealthy(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 0,
		State: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "Error", ExitCode: 1},
		},
	}
	finding := Container(subject(), status, now)
	if finding.Verdict != CrashLooping {
		t.Fatalf("got %q for a container that is terminated right now", finding.Verdict)
	}
	if finding.Reason != "Error" || finding.ExitCode != 1 {
		t.Fatalf("reason %q exit %d: the evidence was dropped", finding.Reason, finding.ExitCode)
	}
}

func TestAContainerOOMKilledRightNowIsNotReadAsACrashLoop(t *testing.T) {
	status := corev1.ContainerStatus{
		Name: "canary",
		State: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "OOMKilled", ExitCode: 137},
		},
	}
	if got := Container(subject(), status, now).Verdict; got != OOMKilled {
		t.Fatalf("got %q", got)
	}
}

// The current state wins over the previous one. A container that has just exited cleanly after a
// crash has recovered, and reporting the old crash would be reporting the past as the present.
func TestTheCurrentTerminationIsReadBeforeTheOlderOne(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 4,
		State: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "Completed", ExitCode: 0},
		},
		LastTerminationState: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "OOMKilled", ExitCode: 137},
		},
	}
	finding := Container(subject(), status, now)
	if finding.Verdict != Healthy {
		t.Fatalf("got %q: the older termination was read over the current one", finding.Verdict)
	}
	if finding.Reason != "Completed" {
		t.Fatalf("reason %q came from the wrong termination", finding.Reason)
	}
}

func TestAnImageThatNeverPulledHasNotRestarted(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 0,
		State:        corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "ImagePullBackOff"}},
	}
	finding := Container(subject(), status, now)
	if finding.Verdict != ImageUnavailable {
		t.Fatalf("got %q", finding.Verdict)
	}
	if finding.Restarts != 0 {
		t.Fatal("the fixture has restarts, which is not what this failure looks like")
	}
	if finding.Reason != "ImagePullBackOff" {
		t.Fatalf("reason %q: this failure is READ OFF the waiting reason and has no terminated state at all", finding.Reason)
	}
}

// A waiting reason nobody here has named is not health. It used to be: the verdict fell through
// to Healthy while the Finding still carried the reason, which is a report that contradicts
// itself and reads as green.
func TestAWaitingReasonThisPackageDoesNotNameIsNotCalledHealthy(t *testing.T) {
	for _, reason := range []string{"CreateContainerConfigError", "InvalidImageName", "inventedByAnAdmissionWebhook"} {
		status := corev1.ContainerStatus{
			Name:  "canary",
			State: corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: reason}},
		}
		finding := Container(subject(), status, now)
		if finding.Verdict != Unclassified {
			t.Fatalf("waiting on %q classified as %q", reason, finding.Verdict)
		}
		if !finding.Verdict.Interesting() {
			t.Fatalf("waiting on %q is not interesting, so nobody hears about it", reason)
		}
		if finding.Reason != reason {
			t.Fatalf("reason %q, and the reason is the only thing a reader has to go on", finding.Reason)
		}
	}
}

// Ordinary startup is not a failure, and this is the correction that matters most in practice:
// every pod is Running and not ready between its process starting and its first readiness probe
// passing, so an instant verdict reports every rolling deploy as broken.
func TestAPodInOrdinaryStartupIsNotReportedAsNeverReady(t *testing.T) {
	if got := Container(subject(), running(2*time.Second, false), now).Verdict; got != Starting {
		t.Fatalf("got %q two seconds into startup", got)
	}
	if Starting.Interesting() {
		t.Fatal("starting is interesting, so every rolling deploy is a page")
	}
}

func TestRunningAndNotReadyPastTheGraceIsTheFailureADashboardShowsGreen(t *testing.T) {
	if got := Container(subject(), running(ReadyGrace+time.Second, false), now).Verdict; got != NeverReady {
		t.Fatalf("got %q, and this is the only failure a dashboard shows green", got)
	}
}

// The boundary itself, both sides, so the comparison cannot be inverted or made non-strict
// without a failure.
func TestTheGraceBoundaryIsWhereItSaysItIs(t *testing.T) {
	if got := Container(subject(), running(ReadyGrace-time.Millisecond, false), now).Verdict; got != Starting {
		t.Fatalf("just inside the grace: got %q", got)
	}
	if got := Container(subject(), running(ReadyGrace, false), now).Verdict; got != NeverReady {
		t.Fatalf("exactly at the grace: got %q", got)
	}
}

// A status with no start time is not evidence that a pod has been unready for a long time.
func TestAContainerWithNoStartTimeIsNotAccusedOfAnything(t *testing.T) {
	status := corev1.ContainerStatus{
		Name:  "canary",
		Ready: false,
		State: corev1.ContainerState{Running: &corev1.ContainerStateRunning{}},
	}
	if got := Container(subject(), status, now).Verdict; got != Starting {
		t.Fatalf("got %q with no StartedAt to measure against", got)
	}
}

func TestRunningAndReadyIsHealthy(t *testing.T) {
	if got := Container(subject(), running(time.Hour, true), now).Verdict; got != Healthy {
		t.Fatalf("got %q for a container that is running and ready", got)
	}
}

// An init container that dies keeps the pod in Init and the app containers never start, so a
// check reading only ContainerStatuses reports a pod with no failures at all.
func TestAnInitContainerFailureIsNotInvisible(t *testing.T) {
	pod := &corev1.Pod{}
	pod.Name = "pod"
	pod.Namespace = "production"
	pod.Status.InitContainerStatuses = []corev1.ContainerStatus{terminated("OOMKilled", 137, 2)}

	findings := Pod(pod, now)
	if len(findings) != 1 {
		t.Fatalf("got %d findings, so the init container was skipped", len(findings))
	}
	if findings[0].Verdict != OOMKilled {
		t.Fatalf("got %q", findings[0].Verdict)
	}
	if findings[0].Restarts != 2 {
		t.Fatalf("restarts %d, and the fixture says 2", findings[0].Restarts)
	}
}

// Two pods with the same name in two namespaces are two pods.
func TestAFindingSaysWhichNamespaceItIsAbout(t *testing.T) {
	pod := &corev1.Pod{}
	pod.Name = "web-0"
	pod.Namespace = "staging"
	pod.Status.ContainerStatuses = []corev1.ContainerStatus{terminated("Error", 1, 1)}

	findings := Pod(pod, now)
	if len(findings) != 1 {
		t.Fatalf("got %d findings", len(findings))
	}
	if findings[0].Namespace != "staging" || findings[0].Pod != "web-0" {
		t.Fatalf("got %q/%q: without both halves two pods are one", findings[0].Namespace, findings[0].Pod)
	}
}

func TestOnlyHealthyAndStartingAreUninteresting(t *testing.T) {
	for _, verdict := range []Verdict{CrashLooping, OOMKilled, ImageUnavailable, NeverReady, Unclassified} {
		if !verdict.Interesting() {
			t.Fatalf("%q is not interesting, so nothing would act on it", verdict)
		}
	}
	for _, verdict := range []Verdict{Healthy, Starting} {
		if verdict.Interesting() {
			t.Fatalf("%q is interesting, so an ordinary deploy is a stream of reports", verdict)
		}
	}
	// A verdict nobody has taught it about is worth a human look rather than silence.
	if !Verdict("something-new").Interesting() {
		t.Fatal("an unrecognised verdict is silently uninteresting, which is how a new failure is missed")
	}
}
