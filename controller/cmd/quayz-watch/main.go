// Command quayz-watch reports which failure each pod in a namespace has hit.
//
// It is deliberately small. The interesting part is classify, which decides, and watch, which
// keeps a connection open. This is the shell that makes them a program somebody can run.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/PNX89/QUAYZ/controller/classify"
	"github.com/PNX89/QUAYZ/controller/watch"
)

func main() {
	kubeconfig := flag.String("kubeconfig", "", "path to a kubeconfig; empty means in-cluster")
	namespace := flag.String("namespace", "default", "namespace to watch")
	resync := flag.Duration("resync", 30*time.Second,
		"how often to re-examine every pod held. Must be non-zero: a pod that is running and not "+
			"ready stops producing events, and the clock is what tells starting from never ready")
	flag.Parse()

	config, err := clientcmd.BuildConfigFromFlags("", *kubeconfig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "kubeconfig: %v\n", err)
		os.Exit(1)
	}
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		fmt.Fprintf(os.Stderr, "client: %v\n", err)
		os.Exit(1)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	report := func(finding classify.Finding) {
		fmt.Printf("%s/%s/%s %s reason=%q exit=%d restarts=%d\n",
			finding.Namespace, finding.Pod, finding.Container, finding.Verdict,
			finding.Reason, finding.ExitCode, finding.Restarts)
	}

	if err := watch.Run(ctx, client, watch.Options{Namespace: *namespace, Resync: *resync}, report); err != nil &&
		ctx.Err() == nil {
		fmt.Fprintf(os.Stderr, "watching: %v\n", err)
		os.Exit(1)
	}
}
