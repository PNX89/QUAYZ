# QUAYZ

**A crash loop and a container the kernel killed for memory are the same thing in `kubectl get
pods`. They are not the same thing, the remedy for one is not the remedy for the other, and the
expensive mistake is not failing to notice a bad deploy: it is noticing and reaching for the
wrong instrument.**

[![CI](https://github.com/PNX89/QUAYZ/actions/workflows/ci.yml/badge.svg)](https://github.com/PNX89/QUAYZ/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![Go 1.27](https://img.shields.io/badge/go-1.27-00add8)](https://go.dev)
[![kind v0.33.0](https://img.shields.io/badge/kind-v0.33.0-326ce5)](https://kind.sigs.k8s.io)
[![Helm v3.21.4](https://img.shields.io/badge/helm-v3.21.4-0f1689)](https://helm.sh)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Five ways a deploy ends with a pod that is not serving, each one produced against a real cluster
that the test run creates and destroys, and each one read with six instruments. The table below
is the whole argument. If you open one file, open [`src/quayz/failures.py`](src/quayz/failures.py).

<!-- instruments:start -->

| state | phase | restarted | waiting reason | terminated reason | exit | endpoints ready |
| --- | --- | --- | --- | --- | --- | --- |
| **healthy, the control** | Running | no | none | none | none | 2 |
| image cannot be pulled | Pending | no | ImagePullBackOff | none | none | 0 |
| crash loop | Running | yes | CrashLoopBackOff | Error | 1 | 0 |
| killed for memory | Running | yes | CrashLoopBackOff | OOMKilled | 137 | 0 |
| alive but never ready | Running | no | none | none | none | 0 |

Two of those rows are the reason this repository exists. A crash loop and a container the kernel
killed for memory agree on four of the six columns, and the two they differ on are the two
nobody looks at first.

| instrument | separates | what it read |
| --- | --- | --- |
| `restart count` | **nothing** | restarted for the crash loop and the OOMKill, and not for the other two failures or for the control. |
| `container logs` | crash loop | one line from the crash loop, the message the process chose on its way out, against zero lines mentioning a problem from the OOMKill |
| `pod phase` | image cannot be pulled | Pending for the image pull and Running for every other failure and for the control |
| `lastState.terminated.reason with exitCode` | crash loop, killed for memory | Error with 1 against OOMKilled with 137. |
| `state.waiting.reason` | image cannot be pulled | ImagePullBackOff for the image pull, and CrashLoopBackOff for BOTH of the other two, which is the same collision one level up |
| `EndpointSlice readiness` | **nothing** | zero endpoints ready for every failure, against two for the control. |
| `every instrument at once, read together` | image cannot be pulled, crash loop, killed for memory, alive but never ready | every failure's row in the matrix is unique, and this is the ONLY entry here that separates the pod which is alive and never ready. |
| `terraform plan over a helm_release` | **nothing** | exit 0, 'No changes', against a Deployment hand-scaled from two replicas to five. |
| `the declared objects against the live ones` | changed by hand afterwards | exit 1 from `helm get values \| helm template \| kubectl diff` against the same hand edit the plan above could not see |

Generated from `src/quayz/failures.py` and `docs/evidence/cluster/summary.json` by
`scripts/readme_block.py`. The left column is declared in code and the right one was read off a
cluster, and a test fails if they disagree: three entries in that table were wrong when they
were finally joined to the measurement, every one of them in the direction that flattered it.

<!-- instruments:end -->

```console
$ uv run python examples/tell_them_apart.py
```

## The two rows that look identical

`CrashLoopBackOff`, a climbing restart count, and a pod that says `Running`. That is what both a
crash loop and an OOMKill show, and the logs make it worse rather than better: an OOMKilled
container's logs end mid-sentence with nothing wrong in them, because the kernel took the process
away rather than the process failing. Measured on the cluster that produced the table above, the
OOMKilled container printed **zero** lines mentioning a problem and the crash-looping one printed
the single line the process chose on its way out.

So a log-based detector reports health for a container that was killed, and a restart-count
detector reports a crash loop. Both are wrong about the cause, and the fix for one is not the fix
for the other: more memory, or less allocation, against a fix to whatever the process was doing.

The one place the difference is written down is the terminated state's reason, and that is why
this repository has a controller rather than a grep.

## The failure a dashboard shows green

A pod that is alive and never ready never restarts, its logs are clean, and `kubectl get pods`
says `Running`. It differs from a healthy pod in exactly one of the six columns above, and that
column reads the same for every failure here. **No single field finds it.** It is found by
reading every instrument and finding only one of them abnormal, which is what
[`controller/classify`](controller/classify/classify.go) does when it falls through every branch.

That is a sharper claim than the one this repository started with, and it replaced a wrong one.
The detector table used to name EndpointSlice readiness as this failure's answer, as though it
identified it. It does not: it reads zero endpoints ready for all four failures, so what it
identifies is a Service that is not serving, and which of four reasons is a question it does not
answer.

## And do not trust the wide output

With no endpoint ready at all, `kubectl get endpointslice -o wide` still printed both pod
addresses in its `ENDPOINTS` column. A check counting that column calls a broken Service healthy,
so every readiness reading here comes from `.endpoints[*].conditions.ready` instead. The
transcript in [`docs/evidence/cluster/alive-but-never-ready.txt`](docs/evidence/cluster/alive-but-never-ready.txt)
carries both, one under the other, so the disagreement is visible rather than described.

## The controller

[`controller/`](controller/) is its own Go module: an informer over pods, a classifier that turns
a container status into a verdict with the evidence that produced it, and a command that prints
them. Three things in client-go changed how it is written, and all three are recorded in the
package comments rather than in a commit message nobody reads again.

- `cache.NewListWatchFromClient` with a nil field selector **segfaults** in client-go v0.37:
  `listwatch.go` calls `fieldSelector.String()` unconditionally.
- The fake clientset returns **nil** from `CoreV1().RESTClient()`, so building an informer from
  the REST plumbing panics against the fake and works against a cluster. The informer factory is
  used instead, so the same code runs against both, which is the property a unit test needs.
- `WatchListClient` has defaulted to **true** since Kubernetes v1.35, so a reflector does not
  call `List` at all: it opens a watch with `SendInitialEvents` and streams the initial state
  through it. A demonstration that counts `List` calls to prove "it resumed rather than relisted"
  counts zero either way and proves nothing. The honest instrument is the `resourceVersion` the
  re-watch asks for, and [`controller/resume`](controller/resume/) reads it off a real
  kube-apiserver under envtest.

**Running and not ready is also what normal startup looks like**, so the never-ready verdict takes
a clock and a grace period. Without one, every pod in every rolling deploy is reported as the
failure this repository is most interested in.

## Drift is a different question, and a plan is the wrong instrument

Somebody scales a Deployment by hand. Every health check passes, because the cluster is healthy:
it is the configuration that is no longer what anybody wrote down.

`terraform plan -detailed-exitcode` over a `helm_release` exits **0** and prints "No changes" for
a Deployment hand-scaled from two replicas to five. That is not a bug in the provider: the
resource compares the chart and its values, and neither changed. The detector that sees it is the
declared objects against the live ones, `helm get values | helm template | kubectl diff`, which
exits **1** on the same edit at the same moment.

And **an error is not the absence of drift**. With `-refresh=false`, the flag everybody adds to
make a drift check fast, terraform reports no changes and exits 0 against a cluster that has been
**deleted**. [`docs/adr/0001-when-a-plan-is-the-wrong-instrument.md`](docs/adr/0001-when-a-plan-is-the-wrong-instrument.md)
records the decision, what it rejected, and when Argo CD is the better answer.

## Recovering

Three ways a broken rollout ends, measured in [`docs/evidence/rollback/`](docs/evidence/rollback/):
`helm upgrade --atomic` exits 1 and leaves two of two pods ready at the previous revision; a bare
`helm upgrade` exits 1 and leaves two ready of three total, which is the trap, because **counting
only ready pods makes a stuck rolling update look healthy**; and `helm rollback` exits 0 and
leaves two of two with five revisions in the history.

## What this does not establish

One node, one namespace, one release, and a cluster that exists for the length of a test run.
Nothing here is evidence about node failure, zone failure, capacity, autoscaling, a service mesh,
or how any of this behaves under load, because a single-node kind cluster cannot demonstrate any
of them. There is no production cluster behind this repository and no claim that there is.

## Running it

```bash
git clone https://github.com/PNX89/QUAYZ.git && cd QUAYZ
uv sync --dev
uv run python examples/tell_them_apart.py
```

That is offline and reads committed evidence. To produce the evidence yourself you need kind,
kubectl and helm, and the harnesses create a cluster and destroy it:

```bash
uv run pytest -o addopts="" -m cluster
```

The supply chain is proved where it can be. The controller image is built twice from nothing and
the manifest digests compared, then built a third time with an SBOM and provenance, and the image
manifest inside that attested index must be the digest the comparison proved before anything is
signed. `SOURCE_DATE_EPOCH` alone is not enough for that: it normalises the image config and does
not rewrite file mtimes inside the layer tars, so two builds a second apart differ in four bytes
of tar octal mtime and the check passes only when they land in the same second, which is the
worst kind of wrong. `rewrite-timestamp=true` is what works.

<!-- toolset:start -->

Part of the Q...Z toolset, all of it designing for the failure that does not announce itself:

- [QUACKZ](https://github.com/PNX89/QUACKZ), deflating a backtest that only looks good because
  it was picked out of two hundred.
- [QUOTEZ](https://github.com/PNX89/QUOTEZ), market data an agent can read and cannot act on.
- [QUELLZ](https://github.com/PNX89/QUELLZ), measuring what prompt-injection containment costs
  in utility as well as in attack rate.
- [QUIDZ](https://github.com/PNX89/QUIDZ), refusing the outbound payment that would have gone
  out twice.
- [QUESTZ](https://github.com/PNX89/QUESTZ), stopping a scraper before it writes a CSV from a
  page that changed shape.
- [QUIZZ](https://github.com/PNX89/QUIZZ), answering what a statistic said at the time, and
  refusing when it cannot.
- [QUARANTINEZ](https://github.com/PNX89/QUARANTINEZ), treating an outcome the venue never
  confirmed as terminal rather than as a retry.
- [QUENCHZ](https://github.com/PNX89/QUENCHZ), deciding in the open what a tool server gets free
  while it is still somebody's subprocess.
- [QUILTZ](https://github.com/PNX89/QUILTZ), proving infrastructure code wrong without a cloud
  account, and saying what that cannot show.
- QUAYZ, this one: telling a crash loop from an OOMKill, and naming the failure that no single
  field finds.
- [QUARRYZ](https://github.com/PNX89/QUARRYZ), keeping every version a statistical office
  published, and failing the build when it quietly issues another.

**On QUILTZ.** QUILTZ argues that a plan is a real check on infrastructure code, and it is. This
repository measures the same instrument being the wrong one: `terraform plan -detailed-exitcode`
over a `helm_release` exits 0 and reports no changes for a Deployment somebody hand-scaled from
two replicas to five, because the resource compares the chart and its values and neither
changed. The two are not in conflict. What a plan checks is what it was pointed at, and knowing
which of those two situations you are in is the difference between a drift check and a habit.

<!-- toolset:end -->

## Licence

MIT. See [LICENSE](LICENSE).
