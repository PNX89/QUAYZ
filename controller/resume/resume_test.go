//go:build envtest

package resume

import (
	"context"
	"sync"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
)

// recorder wraps a ListerWatcher and writes down what each call asked for.
//
// THIS IS THE HONEST INSTRUMENT AND THE OBVIOUS ONE IS NOT. WatchListClient has defaulted to
// TRUE since Kubernetes v1.35, so a reflector with the default feature gates never calls List:
// it opens a watch with SendInitialEvents set and streams the initial state through that. A
// demonstration that counts List calls to show "it resumed rather than relisting" therefore
// counts zero in both cases and proves nothing at all.
//
// What actually distinguishes the two is the resourceVersion the re-watch asks for. A resumption
// names a version. A relist starts from nothing.
type recorder struct {
	inner cache.ListerWatcher

	mu       sync.Mutex
	lists    []string
	watches  []string
	watchers []watch.Interface
}

func (r *recorder) List(options metav1.ListOptions) (runtime.Object, error) {
	r.mu.Lock()
	r.lists = append(r.lists, options.ResourceVersion)
	r.mu.Unlock()
	return r.inner.List(options)
}

func (r *recorder) Watch(options metav1.ListOptions) (watch.Interface, error) {
	r.mu.Lock()
	r.watches = append(r.watches, options.ResourceVersion)
	r.mu.Unlock()

	result, err := r.inner.Watch(options)
	if err == nil {
		r.mu.Lock()
		r.watchers = append(r.watchers, result)
		r.mu.Unlock()
	}
	return result, err
}

func (r *recorder) seen() (lists, watches []string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.lists...), append([]string(nil), r.watches...)
}

// breakTheConnection closes every watch handed out so far, which is what a dropped connection
// looks like to a reflector.
func (r *recorder) breakTheConnection() {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, w := range r.watchers {
		w.Stop()
	}
	r.watchers = nil
}

func TestAReWatchAsksForAResourceVersionRatherThanStartingOver(t *testing.T) {
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		t.Fatalf("client: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	namespace := "default"
	if _, err := client.CoreV1().Pods(namespace).Create(ctx, &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "first", Namespace: namespace},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "c", Image: "busybox"}}},
	}, metav1.CreateOptions{}); err != nil {
		t.Fatalf("creating a pod: %v", err)
	}

	instrument := &recorder{
		inner: cache.NewListWatchFromClient(
			client.CoreV1().RESTClient(),
			"pods",
			namespace,
			// fields.Everything(), NOT nil: a nil selector segfaults in client-go v0.37.
			fields.Everything(),
		),
	}

	store := cache.NewStore(cache.MetaNamespaceKeyFunc)
	reflector := cache.NewReflector(instrument, &corev1.Pod{}, store, 0)
	go reflector.Run(ctx.Done())

	waitFor(t, 10*time.Second, func() bool {
		_, watches := instrument.seen()
		return len(watches) >= 1
	}, "the reflector never opened a watch")

	// Something happens while the watch is open, so the API server's resourceVersion advances
	// past whatever the first watch started from.
	if _, err := client.CoreV1().Pods(namespace).Create(ctx, &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "second", Namespace: namespace},
		Spec:       corev1.PodSpec{Containers: []corev1.Container{{Name: "c", Image: "busybox"}}},
	}, metav1.CreateOptions{}); err != nil {
		t.Fatalf("creating a second pod: %v", err)
	}
	waitFor(t, 10*time.Second, func() bool { return len(store.List()) == 2 }, "the store never saw both pods")

	instrument.breakTheConnection()

	waitFor(t, 20*time.Second, func() bool {
		_, watches := instrument.seen()
		return len(watches) >= 2
	}, "the reflector never re-watched after the connection dropped")

	lists, watches := instrument.seen()
	t.Logf("List calls asked for resourceVersions %q", lists)
	t.Logf("Watch calls asked for resourceVersions %q", watches)

	last := watches[len(watches)-1]
	if last == "" || last == "0" {
		t.Fatalf(
			"the re-watch asked for resourceVersion %q, which is a start-from-scratch rather "+
				"than a resumption. Every object would be redelivered", last,
		)
	}

	// And the store did not lose anything across the break, which is the consequence a caller
	// actually cares about.
	if got := len(store.List()); got != 2 {
		t.Fatalf("the store holds %d objects after the reconnect, want 2", got)
	}
}

// The trap, kept as a test so nobody replaces the instrument above with the obvious one.
func TestCountingListCallsWouldProveNothing(t *testing.T) {
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		t.Fatalf("client: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	instrument := &recorder{
		inner: cache.NewListWatchFromClient(
			client.CoreV1().RESTClient(), "pods", "default", fields.Everything(),
		),
	}
	store := cache.NewStore(cache.MetaNamespaceKeyFunc)
	reflector := cache.NewReflector(instrument, &corev1.Pod{}, store, 0)
	go reflector.Run(ctx.Done())

	waitFor(t, 10*time.Second, func() bool {
		_, watches := instrument.seen()
		return len(watches) >= 1
	}, "the reflector never opened a watch")

	lists, watches := instrument.seen()
	if len(watches) == 0 {
		t.Fatal("no watch was opened at all")
	}
	t.Logf("with WatchListClient at its default, the reflector made %d List calls and %d Watch calls",
		len(lists), len(watches))

	// This is the point rather than an incidental observation: on a default client the initial
	// state arrives through the watch, so List is not the instrument. If a future client-go
	// changes that default this test will start failing, and the comment above the recorder
	// needs rewriting rather than this assertion relaxing.
	if len(lists) > 0 {
		t.Skipf(
			"this client-go called List %d times, so WatchListClient is no longer defaulting on. "+
				"The resumption test above is still correct; the explanation of why List is a "+
				"useless instrument needs updating", len(lists),
		)
	}
}

func waitFor(t *testing.T, limit time.Duration, condition func() bool, message string) {
	t.Helper()
	deadline := time.Now().Add(limit)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal(message)
}
