package watch

import (
	"context"
	"sync"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	"github.com/PNX89/QUAYZ/controller/classify"
)

// THE FAKE CLIENTSET IS A STATED LIMIT OF THIS HARNESS, NOT A SUBSTITUTE FOR A CLUSTER. It
// serves objects and watches, and it does not model resourceVersion semantics: a watch against
// it does not resume from a version, it replays. So everything here tests the WIRING and the
// DECISIONS, and the resumption claim is proved against envtest instead, where a real
// kube-apiserver is answering.

// Short, because every test here waits on it and none of them is about the interval.
const testResync = 200 * time.Millisecond

func options(namespace string) Options {
	return Options{Namespace: namespace, Resync: testResync}
}

func podIn(namespace, name string, statuses ...corev1.ContainerStatus) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace},
		Status:     corev1.PodStatus{ContainerStatuses: statuses},
	}
}

func pod(name string, statuses ...corev1.ContainerStatus) *corev1.Pod {
	return podIn("default", name, statuses...)
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
		State: corev1.ContainerState{Running: &corev1.ContainerStateRunning{StartedAt: metav1.Now()}},
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

// running starts a watch and returns the collector plus a stop function, so a test can drive the
// cluster while it is being watched rather than only setting it up in advance.
func running(t *testing.T, client *fake.Clientset, namespace string) (*collector, func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	found := &collector{}
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = Run(ctx, client, options(namespace), found.report)
	}()
	return found, func() {
		cancel()
		<-done
	}
}

// waitFor polls until the collector holds at least n findings, and reports whether it got there.
// Polling rather than sleeping, so a fast machine is not made to wait for a slow one.
func waitFor(found *collector, n int, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if len(found.all()) >= n {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return len(found.all()) >= n
}

func runFor(t *testing.T, client *fake.Clientset, wait time.Duration) []classify.Finding {
	t.Helper()
	found, stop := running(t, client, "default")
	waitFor(found, 1, wait)
	stop()
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
	if findings[0].Namespace != "default" {
		t.Fatalf("namespace %q: a finding that cannot say where is one a reader has to go and find", findings[0].Namespace)
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
	client := fake.NewSimpleClientset(pod("broken", oomKilled()))
	found, stop := running(t, client, "default")
	defer stop()

	if !waitFor(found, 1, 3*time.Second) {
		t.Fatal("nothing was reported at all")
	}

	// Three more restarts, same verdict, plus at least one resync in the same window.
	for restarts := int32(4); restarts <= 6; restarts++ {
		status := oomKilled()
		status.RestartCount = restarts
		updated := pod("broken", status)
		if _, err := client.CoreV1().Pods("default").Update(context.Background(), updated, metav1.UpdateOptions{}); err != nil {
			t.Fatalf("update: %v", err)
		}
		time.Sleep(150 * time.Millisecond)
	}

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
	found, stop := running(t, client, "default")
	defer stop()

	if !waitFor(found, 1, 3*time.Second) {
		t.Fatal("the first failure was never reported")
	}
	if _, err := client.CoreV1().Pods("default").Update(context.Background(), pod("broken", oomKilled()), metav1.UpdateOptions{}); err != nil {
		t.Fatalf("update: %v", err)
	}
	if !waitFor(found, 2, 3*time.Second) {
		t.Fatal("the verdict changed and only one report came out")
	}

	findings := found.all()
	if findings[0].Verdict != classify.CrashLooping || findings[1].Verdict != classify.OOMKilled {
		t.Fatalf("got %q then %q", findings[0].Verdict, findings[1].Verdict)
	}
}

// A FAILURE THAT COMES BACK IS NEWS. The deduplication key used to be permanent, so a pod that
// broke, recovered and broke again the same way was reported once, hours earlier.
//
// This is deduplication state and not a retraction: nothing here tells a consumer that a pod it
// was told about is now fine, and the package says so rather than implying otherwise.
func TestAFailureThatRecursAfterARecoveryIsHeardAgain(t *testing.T) {
	client := fake.NewSimpleClientset(pod("flapping", oomKilled()))
	found, stop := running(t, client, "default")
	defer stop()

	if !waitFor(found, 1, 3*time.Second) {
		t.Fatal("the first failure was never reported")
	}
	ctx := context.Background()
	if _, err := client.CoreV1().Pods("default").Update(ctx, pod("flapping", healthy()), metav1.UpdateOptions{}); err != nil {
		t.Fatalf("recovery update: %v", err)
	}
	// The recovery has to be processed before the relapse, or nothing was forgotten and this
	// test passes for the wrong reason. Waiting on the fact rather than on a duration is not
	// available here, so it waits for longer than the informer needs and asserts the count.
	time.Sleep(400 * time.Millisecond)
	if got := len(found.all()); got != 1 {
		t.Fatalf("the recovery itself produced a report: %d findings", got)
	}
	if _, err := client.CoreV1().Pods("default").Update(ctx, pod("flapping", oomKilled()), metav1.UpdateOptions{}); err != nil {
		t.Fatalf("relapse update: %v", err)
	}
	if !waitFor(found, 2, 3*time.Second) {
		t.Fatal("the pod broke again the same way and nobody was told")
	}
}

// TWO PODS WITH THE SAME NAME IN TWO NAMESPACES ARE TWO PODS. With an empty namespace the watch
// covers the whole cluster, and a key without the namespace in it reported the first and
// silently swallowed the second.
func TestTwoPodsWithTheSameNameInTwoNamespacesAreBothReported(t *testing.T) {
	client := fake.NewSimpleClientset(
		podIn("staging", "web-0", oomKilled()),
		podIn("production", "web-0", oomKilled()),
	)
	found, stop := running(t, client, metav1.NamespaceAll)
	defer stop()

	if !waitFor(found, 2, 3*time.Second) {
		t.Fatalf("got %d findings for two broken pods in two namespaces", len(found.all()))
	}
	namespaces := map[string]bool{}
	for _, finding := range found.all() {
		namespaces[finding.Namespace] = true
	}
	if !namespaces["staging"] || !namespaces["production"] {
		t.Fatalf("reported namespaces %v", namespaces)
	}
}

func TestAWatchWithNowhereToReportIsRefused(t *testing.T) {
	err := Run(context.Background(), fake.NewSimpleClientset(), options("default"), nil)
	if err == nil {
		t.Fatal("a nil reporter was accepted, so the watch would run and discard everything")
	}
}

// A pod that is Running and not ready stops producing events once it settles. With no resync it
// is never looked at again, so a pod that was still inside classify.ReadyGrace when it was last
// seen never becomes a never-ready finding: the failure this repository is most interested in
// would be unreachable, and the watch would look like it was working.
func TestAWatchThatNeverLooksAgainIsRefused(t *testing.T) {
	found := &collector{}
	err := Run(context.Background(), fake.NewSimpleClientset(), Options{Namespace: "default"}, found.report)
	if err == nil {
		t.Fatal("a zero resync was accepted, and never-ready is unreachable with one")
	}
}
