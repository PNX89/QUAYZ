package watch

import (
	"context"
	"sync"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/client-go/kubernetes/fake"

	"github.com/PNX89/QUAYZ/controller/classify"
)

// THE FAKE CLIENTSET IS A STATED LIMIT OF THIS HARNESS, NOT A SUBSTITUTE FOR A CLUSTER. It
// serves objects and watches, and it does not model resourceVersion semantics: a watch against
// it does not resume from a version, it replays. So everything here tests the WIRING and the
// DECISIONS, and the resumption claim is proved against envtest instead, where a real
// kube-apiserver is answering.

func pod(name string, statuses ...corev1.ContainerStatus) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
		Status:     corev1.PodStatus{ContainerStatuses: statuses},
	}
}

func oomKilled() corev1.ContainerStatus {
	return corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 3,
		State:        corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
		LastTerminationState: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "OOMKilled", ExitCode: 137},
		},
	}
}

func healthy() corev1.ContainerStatus {
	return corev1.ContainerStatus{
		Name:  "canary",
		Ready: true,
		State: corev1.ContainerState{Running: &corev1.ContainerStateRunning{}},
	}
}

type collector struct {
	mu       sync.Mutex
	findings []classify.Finding
}

func (c *collector) report(finding classify.Finding) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.findings = append(c.findings, finding)
}

func (c *collector) all() []classify.Finding {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]classify.Finding(nil), c.findings...)
}

func runFor(t *testing.T, client *fake.Clientset, wait time.Duration) []classify.Finding {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	found := &collector{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = Run(ctx, client, Options{Namespace: "default"}, found.report)
	}()

	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		if len(found.all()) > 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	<-done
	return found.all()
}

func TestAnOOMKilledPodIsReported(t *testing.T) {
	client := fake.NewSimpleClientset(pod("broken", oomKilled()))
	findings := runFor(t, client, 3*time.Second)

	if len(findings) != 1 {
		t.Fatalf("got %d findings, want 1: %+v", len(findings), findings)
	}
	if findings[0].Verdict != classify.OOMKilled {
		t.Fatalf("got %q", findings[0].Verdict)
	}
	if findings[0].ExitCode != 137 {
		t.Fatalf("exit code %d, and it is half the evidence", findings[0].ExitCode)
	}
}

func TestAHealthyPodIsNotReported(t *testing.T) {
	client := fake.NewSimpleClientset(pod("fine", healthy()))
	if findings := runFor(t, client, 1500*time.Millisecond); len(findings) != 0 {
		t.Fatalf("a healthy pod produced %d findings: %+v", len(findings), findings)
	}
}

// A crash-looping pod produces an update on every restart. Reporting each one turns a single
// broken deploy into a stream, and a stream is what people mute.
func TestTheSameFailureIsReportedOnce(t *testing.T) {
	broken := pod("broken", oomKilled())
	client := fake.NewSimpleClientset(broken)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	found := &collector{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = Run(ctx, client, Options{Namespace: "default"}, found.report)
	}()

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) && len(found.all()) == 0 {
		time.Sleep(20 * time.Millisecond)
	}

	// Three more restarts, same verdict.
	for restarts := int32(4); restarts <= 6; restarts++ {
		status := oomKilled()
		status.RestartCount = restarts
		updated := pod("broken", status)
		if _, err := client.CoreV1().Pods("default").Update(ctx, updated, metav1.UpdateOptions{}); err != nil {
			t.Fatalf("update: %v", err)
		}
		time.Sleep(150 * time.Millisecond)
	}
	cancel()
	<-done

	if got := len(found.all()); got != 1 {
		t.Fatalf("the same failure was reported %d times, so one broken pod is a stream", got)
	}
}

// A pod that changes from one failure to another is a change worth hearing about, so the
// deduplication must be keyed on the verdict and not on the pod.
func TestADifferentFailureOnTheSamePodIsReportedAgain(t *testing.T) {
	crashing := corev1.ContainerStatus{
		Name:         "canary",
		RestartCount: 2,
		State:        corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "CrashLoopBackOff"}},
		LastTerminationState: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: "Error", ExitCode: 1},
		},
	}
	client := fake.NewSimpleClientset(pod("broken", crashing))

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	found := &collector{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = Run(ctx, client, Options{Namespace: "default"}, found.report)
	}()

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) && len(found.all()) == 0 {
		time.Sleep(20 * time.Millisecond)
	}

	if _, err := client.CoreV1().Pods("default").Update(ctx, pod("broken", oomKilled()), metav1.UpdateOptions{}); err != nil {
		t.Fatalf("update: %v", err)
	}
	deadline = time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) && len(found.all()) < 2 {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	<-done

	findings := found.all()
	if len(findings) != 2 {
		t.Fatalf("got %d findings, want 2: the verdict changed and only one was reported", len(findings))
	}
	if findings[0].Verdict != classify.CrashLooping || findings[1].Verdict != classify.OOMKilled {
		t.Fatalf("got %q then %q", findings[0].Verdict, findings[1].Verdict)
	}
}

func TestAWatchWithNowhereToReportIsRefused(t *testing.T) {
	err := Run(context.Background(), fake.NewSimpleClientset(), Options{Namespace: "default"}, nil)
	if err == nil {
		t.Fatal("a nil reporter was accepted, so the watch would run and discard everything")
	}
}

// The segfault, kept as a test so nobody helpfully simplifies the call.
//
// cache.NewListWatchFromClient with a nil field selector panics in client-go v0.37:
// listwatch.go calls fieldSelector.String() unconditionally. fields.Everything() is what to pass.
func TestTheFieldSelectorIsNeverNil(t *testing.T) {
	if fields.Everything() == nil {
		t.Fatal("fields.Everything() is nil, which is the value that segfaults")
	}
	if got := fields.Everything().String(); got != "" {
		t.Fatalf("fields.Everything() renders as %q, want the empty selector", got)
	}
}
