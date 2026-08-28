# ADR 0001: When a plan is the wrong instrument, and when Argo CD is the right one

**Status:** accepted, 28 August 2026.

## The decision

**Drift is detected by comparing the declared objects against the live ones, not by
`terraform plan` over a `helm_release`.** The Terraform plan stays, because it is the deploy
diff and it belongs in the pipeline log, but it is not the drift detector and this repository
does not pretend it is.

## Why, measured rather than argued

A Deployment declared with two replicas was scaled to five by hand. Then:

| instrument | exit | what it said |
| --- | --- | --- |
| `terraform plan -detailed-exitcode` | **0** | "No changes. Your infrastructure matches the configuration" |
| `kubectl diff` against the rendered chart | **1** | `-  replicas: 5` / `+  replicas: 2` |

That is not a bug in the provider. `helm_release` models a release: a chart, a set of values and
a revision. The values did not change, so the release did not change. What changed was an object
in the cluster, which the resource does not model and never claimed to.

The consequence is the part worth stating plainly: **a drift detector built on `helm_release`
alone cannot see the drift it exists to catch**, and it will report healthy while doing it.

## The second finding, which is about how the check is written rather than which one

`terraform plan -detailed-exitcode` returns 0 for no diff, 2 for a diff and **1 for an error**,
and `kubectl diff` returns 0, 1 and greater than 1 respectively. An error is not the absence of
drift, and the way that goes wrong is specific:

    $ terraform plan -detailed-exitcode                  # cluster deleted
    exit 2, Plan: 1 to add

    $ terraform plan -detailed-exitcode -refresh=false   # cluster deleted
    exit 0, No changes. Your infrastructure matches the configuration.

`-refresh=false` is the flag everybody reaches for to make a drift check fast. It turns a cluster
that no longer exists into a clean bill of health. It is never used here, and a test asserts it
is passed to terraform on exactly one line, the one that demonstrates why it is forbidden.

## Rejected alternatives

**Manage the Deployment with the kubernetes provider instead of through the chart.** This would
make the hand edit real drift that `terraform plan` reports, which is the tidiest answer on
paper. Rejected because it means the chart is no longer the deploy path: two things would own the
same object, and the first `helm upgrade` would fight the next `terraform apply`. Swapping a
detection gap for a reconciliation conflict is not an improvement.

**A `check` block asserting the live replica count.** Rejected because check blocks report as
warnings and do not change the exit code, so a pipeline would go green over the finding. A drift
check whose result is a warning is a drift check nobody acts on.

**Drop the Terraform leg and use `kubectl diff` alone.** Rejected because the plan is genuinely
useful for the thing it does model: it is the deploy diff, visible in the CI log before anything
is applied, and it catches a changed chart version or a changed value, which is the ordinary case.

## When Argo CD is the right answer instead of any of this

Said plainly, because the honest version of this repository's argument leads there.

Everything above is **polling**: something has to run the check, on a schedule somebody chose,
and drift exists undetected until the next run. Argo CD and Flux invert that. They hold the
declared state and reconcile continuously, so the window between an edit and its detection is
seconds rather than however long the cron interval is, and they can be configured to put the
declared state back rather than merely report the difference.

**If a cluster is shared, if people have `kubectl edit` and use it, or if the answer to drift
should be automatic repair rather than a report, a GitOps controller is the right tool and this
is not.** What is here is the smaller thing that fits a pipeline nobody wants to run a controller
for: a deploy diff in the build log, and a drift check that runs when the pipeline runs.

I have not operated Argo CD or Flux. That sentence belongs in this ADR rather than in an
interview, and the reason this section exists is that "why not GitOps" is the first question any
competent reviewer asks about a repository like this one.

## What this does not establish

One node, one namespace, one release. Nothing here says anything about drift detection across
many clusters, about a controller's behaviour under contention, or about how any of this performs
when the object count is large. The cluster is created and destroyed by the test run, so there is
no long-lived state for drift to accumulate in, and every measurement above is of an edit made
seconds earlier.
