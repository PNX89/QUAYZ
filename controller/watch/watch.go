// Package watch runs an informer over pods and reports what classify makes of each one.
//
// WHY AN INFORMER AND NOT A POLL. A poll answers "what is broken now" at whatever interval
// somebody chose, and misses anything that broke and recovered in between. An informer holds a
// watch open and is told, which is the difference between finding out and looking.
//
// TWO THINGS IN client-go BIT HARD ENOUGH TO CHANGE HOW THIS IS WRITTEN, and both are recorded
// here rather than in a commit message nobody reads again.
//
// FIRST, cache.NewListWatchFromClient with a nil field selector SEGFAULTS in client-go v0.37:
// listwatch.go calls fieldSelector.String() unconditionally. It is not a nil check away from
// working, it is a nil dereference. fields.Everything() is the value to pass.
//
// SECOND, WatchListClient has defaulted to TRUE since Kubernetes v1.35. A reflector with the
// default feature gates does not call List at all: it opens a watch with SendInitialEvents set
// and streams the initial state through it. So a demonstration that counts List calls to prove
// "it resumed rather than relisting" counts zero either way and proves nothing. The honest
// instrument is the resourceVersion the re-watch asks for, which is what the envtest test reads.
//
// AND A THIRD, FOUND BY RUNNING IT. The first version built the informer from
// cache.NewListWatchFromClient(client.CoreV1().RESTClient(), ...). Against a real cluster that
// is fine. Against the fake clientset it panics with a nil pointer dereference inside
// rest.NewRequest, because the fake's RESTClient() returns nil: it implements the typed methods
// and not the REST plumbing underneath them.
//
// The informer factory is used instead. It builds its ListerWatcher from the typed client's own
// List and Watch, so the same code runs against the fake and against a real API server, which is
// the property a unit test needs. Reaching for the RESTClient was the more "low level" choice
// and it was the one that could not be tested.
package watch

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"

	"github.com/PNX89/QUAYZ/controller/classify"
)

// Reporter receives every finding worth acting on. Taking a function rather than writing to a
// log means the tests observe exactly what a real caller would.
type Reporter func(classify.Finding)

// Options are what a caller has to decide, all of them without defaults on purpose.
type Options struct {
	Namespace string
	// Resync is how often the informer re-delivers everything it holds. Zero means never, which
	// is the right answer here: this reports state changes, and a resync would re-report a pod
	// that has been crash-looping since yesterday every interval.
	Resync time.Duration
}

// Run watches pods until the context is cancelled, reporting each interesting finding once.
//
// Once, not once per event. A crash-looping pod produces an update on every restart, and a
// reporter called on each of them turns one broken deploy into a stream. The seen map is keyed
// by pod, container and verdict, so a pod that goes from crash-looping to OOMKilled is reported
// again, which is a change worth hearing about.
func Run(ctx context.Context, client kubernetes.Interface, options Options, report Reporter) error {
	if report == nil {
		return fmt.Errorf("no reporter: a watch whose findings go nowhere is a busy loop")
	}

	seen := map[string]bool{}
	handle := func(object any) {
		pod, ok := object.(*corev1.Pod)
		if !ok {
			return
		}
		for _, finding := range classify.Pod(pod) {
			if !finding.Verdict.Interesting() {
				continue
			}
			key := fmt.Sprintf("%s/%s/%s", finding.Pod, finding.Container, finding.Verdict)
			if seen[key] {
				continue
			}
			seen[key] = true
			report(finding)
		}
	}

	factory := informers.NewSharedInformerFactoryWithOptions(
		client,
		options.Resync,
		informers.WithNamespace(options.Namespace),
		// fields.Everything(), NOT nil. A nil field selector segfaults in client-go v0.37:
		// listwatch.go calls fieldSelector.String() without checking it. The selector is set
		// explicitly here even though it selects everything, so nobody removes the argument and
		// rediscovers that.
		informers.WithTweakListOptions(func(list *metav1.ListOptions) {
			list.FieldSelector = fields.Everything().String()
		}),
	)

	informer := factory.Core().V1().Pods().Informer()
	if _, err := informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    handle,
		UpdateFunc: func(_, object any) { handle(object) },
	}); err != nil {
		return fmt.Errorf("adding the event handler: %w", err)
	}

	factory.Start(ctx.Done())
	factory.WaitForCacheSync(ctx.Done())
	<-ctx.Done()
	return ctx.Err()
}
