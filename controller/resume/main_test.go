//go:build envtest

// Package resume proves what happens to a watch when the connection drops, against a real
// kube-apiserver rather than a fake.
//
// WHY THIS IS A SEPARATE PACKAGE BEHIND A BUILD TAG. It needs kube-apiserver and etcd on disk,
// fetched by setup-envtest, which is 167 MB a stranger cloning this repository has not got. The
// tag keeps `go test ./...` working for them and this suite honest about what it requires.
//
//	go run sigs.k8s.io/controller-runtime/tools/setup-envtest@latest use
//	KUBEBUILDER_ASSETS=$(setup-envtest use -p path) go test -tags envtest ./resume/...
//
// ENVTEST DOES NOT CLEAN UP AFTER ITSELF AND THAT IS WHY TestMain LOOKS LIKE THIS. It starts
// etcd and kube-apiserver as child processes and does not put them in a process group it kills.
// macOS has no PDEATHSIG, so a panic, a Ctrl-C or an OOM leaves roughly 255 MiB of them running
// for ever, plus a temp data directory. Measured while building this: a run that panicked left a
// pair alive ten minutes later. Across a day of iterating that is gigabytes and a machine that
// mysteriously slows down.
//
// So the environment is stopped on the normal path, on a panic, and on a signal. If one escapes
// anyway, the escape hatch is `pkill -f envtest-bins`, and it is in the README rather than only
// here.
package resume

import (
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"testing"

	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

var config *rest.Config

func TestMain(m *testing.M) {
	environment := &envtest.Environment{}
	if assets := os.Getenv("KUBEBUILDER_ASSETS"); assets != "" {
		environment.BinaryAssetsDirectory = filepath.Clean(assets)
	}

	stopped := make(chan struct{})
	stop := func() {
		select {
		case <-stopped:
			return
		default:
			close(stopped)
		}
		if err := environment.Stop(); err != nil {
			fmt.Fprintf(os.Stderr, "stopping envtest: %v\n", err)
		}
	}

	// A signal handler, because Ctrl-C during a slow test is the ordinary way this leaks.
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-signals
		stop()
		os.Exit(130)
	}()

	started, err := environment.Start()
	if err != nil {
		fmt.Fprintf(os.Stderr, "starting envtest: %v\n", err)
		fmt.Fprintln(os.Stderr, "fetch the binaries: setup-envtest use, then set KUBEBUILDER_ASSETS")
		os.Exit(1)
	}
	config = started

	// A panic in a test must not skip the stop, so the run is wrapped rather than deferred
	// around an os.Exit, which does not run defers at all.
	code := func() (code int) {
		defer func() {
			if recovered := recover(); recovered != nil {
				stop()
				panic(recovered)
			}
		}()
		return m.Run()
	}()

	stop()
	os.Exit(code)
}
