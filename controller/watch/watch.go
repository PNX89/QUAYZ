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
// working, it is a nil dereference. fields.Everything() is the value to pass. That is recorded
// here and NOT guarded in the code any more, which is a correction: this file used to set the
// field selector through WithTweakListOptions with a comment saying it stopped the segfault. It
// did not. fields.Everything().String() is the empty string, so the tweak set the default, and
// the crash is on a code path this package does not take. A guard against a bug that cannot
// happen here reads as protection and is not, so it is gone and the lesson stayed.
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
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"

	"github.com/PNX89/QUAYZ/controller/classify"
)

// findingPrefix identifies the container a finding is about. It ends in a separator so that one
// container's key cannot be a prefix of another's: `canary` and `canary2` are different
// containers, and forgetting the first must not forget the second.
func findingPrefix(finding classify.Finding) string {
	return fmt.Sprintf("%s/%s/%s/", finding.Namespace, finding.Pod, finding.Container)
}

// Reporter receives every finding worth acting on. Taking a function rather than writing to a
// log means the tests observe exactly what a real caller would.
type Reporter func(classify.Finding)

// Options are what a caller has to decide, all of them without defaults on purpose.
type Options struct {
	// Namespace to watch. The empty string is every namespace, which is why a Finding carries
	// the namespace and the deduplication key includes it: two pods called web-0 in two
	// namespaces are two pods, and a key without the namespace reports one of them and swallows
	// the other.
	Namespace string
	// Resync is how often the informer re-delivers everything it holds, and it MUST NOT BE ZERO.
	//
	// This is a correction. It used to say zero was right, on the argument that a resync would
	// re-report a pod that had been crash-looping since yesterday. The deduplication below
	// already prevents that, so the argument was for a cost that does not exist, and the cost of
	// zero is real: a pod that is Running and not ready settles, stops producing events, and is
	// never looked at again. If it was still inside classify.ReadyGrace when the last event
	// arrived, the failure this repository is most interested in is never reported at all. A
	// resync is what gives the clock a second look.
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
	if options.Resync <= 0 {
		return fmt.Errorf(
			"resync is %s: a pod that is running and not ready stops producing events, so with "+
				"no resync one that was still starting when it was last seen is never looked at "+
				"again and never-ready is unreachable", options.Resync)
	}

	seen := map[string]bool{}
	forget := func(finding classify.Finding) {
		for key := range seen {
			if strings.HasPrefix(key, findingPrefix(finding)) {
				delete(seen, key)
			}
		}
	}
	handle := func(object any) {
		pod, ok := object.(*corev1.Pod)
		if !ok {
			return
		}
		for _, finding := range classify.Pod(pod, time.Now()) {
			if !finding.Verdict.Interesting() {
				// Recovered, or never broken. Either way the container's earlier verdicts are
				// forgotten, so the same failure happening AGAIN is heard again. This is
				// deduplication state and not a retraction: nothing here tells a consumer that a
				// pod it was told about is now fine, and a consumer that needs to know that
				// needs an event carrying the recovery rather than the absence of one.
				forget(finding)
				continue
			}
			key := findingPrefix(finding) + string(finding.Verdict)
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
	)

	informer := factory.Core().V1().Pods().Informer()
	if _, err := informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    handle,
		UpdateFunc: func(_, object any) { handle(object) },
		// A deleted pod's verdicts go with it. Without this, a StatefulSet replacing web-0 with
		// a new web-0 that fails the same way is deduplicated against the dead one's key and
		// nobody is told.
		DeleteFunc: func(object any) {
			if pod, ok := object.(*corev1.Pod); ok {
				for _, finding := range classify.Pod(pod, time.Now()) {
					forget(finding)
				}
			}
		},
	}); err != nil {
		return fmt.Errorf("adding the event handler: %w", err)
	}

	factory.Start(ctx.Done())
	factory.WaitForCacheSync(ctx.Done())
	<-ctx.Done()
	return ctx.Err()
}
