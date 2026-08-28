# The four binaries this repository's harnesses need, so a stranger installs one thing.
#
# WHAT THIS IS FOR, HONESTLY. `scripts/measure_failures.sh`, `measure_rollback.sh` and
# `measure_drift.sh` need kind, kubectl, helm and terraform. That is four downloads before
# anybody can reproduce a single number in docs/evidence, and four chances to end up on a
# different version from the one the evidence was made with. This carries exactly those four, at
# exactly those versions.
#
# WHAT IT IS NOT. It does not run a cluster by itself. kind drives Docker, so creating a cluster
# from in here needs the host's Docker socket mounted, and that is a real thing to hand a
# container rather than a detail to gloss over. What runs with no socket at all is everything
# that does not touch a cluster: helm lint, helm template, terraform validate, terraform fmt.
# The CI job exercises that half, because it is the half that can be proved without giving a
# build agent the daemon.
#
# EVERY BINARY IS VERIFIED AGAINST THE PUBLISHER'S OWN CHECKSUM, not merely pinned by version. A
# version is what you asked for; a checksum is what you got. A repository whose subject is
# supply chain provenance downloading four binaries on trust would be arguing one thing and
# doing another.
#
# Refresh a checksum from the publisher rather than from the file you just downloaded:
#   kubectl    https://dl.k8s.io/release/<ver>/bin/linux/amd64/kubectl.sha256
#   kind       https://github.com/kubernetes-sigs/kind/releases/download/<ver>/kind-linux-amd64.sha256sum
#   helm       https://get.helm.sh/helm-<ver>-linux-amd64.tar.gz.sha256sum
#   terraform  https://releases.hashicorp.com/terraform/<ver>/terraform_<ver>_SHA256SUMS
#
# Every checksum here is for linux/amd64. Changing the platform means changing all four, and
# leaving them while changing the platform gives a build that fails at the checksum, which is
# the correct failure and the reason they are here.

# debian:13-slim, EXPLICITLY linux/amd64, and that is not a detail.
#
# THE FIRST VERSION OF THIS FILE PRODUCED AN IMAGE THAT LIED ABOUT ITSELF. It downloaded the
# linux/amd64 binaries, which is what the checksums below cover, and built on an arm64 machine.
# The build succeeded, every command in it ran, and `docker image inspect` reported
# linux/arm64 while all four binaries were x86-64 ELF. It ran only because the local VM has
# qemu emulation. On a host without it every command fails with "exec format error", and an
# arm64 host would pull it precisely because the metadata claims arm64.
#
# A build succeeding is not an image working. The platform is declared so the metadata is true,
# and the consequence is stated rather than hidden: this is an amd64 image. It matches CI, and
# on an arm64 machine it runs under emulation, which is slower and works.
#
# AND DECLARING IT IS NOT ENOUGH UNDER THE LEGACY BUILDER, which is the second half of the same
# finding. Built with plain `docker build` on an arm64 host, the image STILL reports
# linux/arm64 while `kind version` inside it prints linux/amd64: the legacy builder takes the
# image architecture from the host and ignores this line. buildx honours it, so the CI job
# builds with buildx and ASSERTS the architecture, which is the only place that assertion can
# be made truthfully.
FROM --platform=linux/amd64 debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS fetch

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /fetch

# The versions the evidence in docs/evidence was produced with. Changing one here without
# regenerating that evidence would make the numbers describe a toolchain nobody is running.
ARG KUBECTL_VERSION=v1.37.0
ARG KIND_VERSION=v0.33.0
ARG HELM_VERSION=v3.21.4
ARG TERRAFORM_VERSION=1.16.0

ARG KUBECTL_SHA256=6129359f4e1f3848a5572ccb0b26cf28b8ca08cef38c95a765b2f64a2c961a2f
ARG KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
ARG HELM_SHA256=61f88ab166748cb19604d7884cb100ae9ccb13804ddeb98e08af167eacbb6a14
ARG TERRAFORM_SHA256=41d05b927aa174f15d1228c4eba832a323b716c68b415ef5a00179f46cc602d3

# One RUN per binary, so a checksum failure names which one. Bundling them into a single layer
# would be smaller and would report "the download step failed".
RUN curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && echo "${KUBECTL_SHA256}  kubectl" | sha256sum -c - \
 && chmod +x kubectl

RUN curl -fsSLo kind "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64" \
 && echo "${KIND_SHA256}  kind" | sha256sum -c - \
 && chmod +x kind

RUN curl -fsSLo helm.tar.gz "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
 && echo "${HELM_SHA256}  helm.tar.gz" | sha256sum -c - \
 && tar -xzf helm.tar.gz linux-amd64/helm --strip-components=1 \
 && chmod +x helm

RUN curl -fsSLo terraform.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
 && echo "${TERRAFORM_SHA256}  terraform.zip" | sha256sum -c - \
 && unzip -q terraform.zip terraform \
 && chmod +x terraform

# debian:13-slim, amd64 for the same reason as the stage above.
FROM --platform=linux/amd64 debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

# ca-certificates and git only. This is a place to run four binaries, not a development
# environment, and every package added here is another thing to keep patched.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 toolbox

COPY --from=fetch /fetch/kubectl /fetch/kind /fetch/helm /fetch/terraform /usr/local/bin/

# NOT root, even though this never faces a network. A toolbox run with a Docker socket mounted
# has the daemon's authority, and root inside it is one fewer thing between a mistake and the
# host.
USER toolbox
WORKDIR /work

# Terraform's CLI is BUSL 1.1, licensed by IBM since 2024, and its Additional Use Grant excludes
# offering the work to third parties on a hosted or embedded basis in competition with their paid
# product. This image is built and used by whoever clones this repository; it is not published to
# a registry and it is not a hosted service. Both of those lines matter and the CI job that
# builds this does NOT push it.
LABEL org.opencontainers.image.source="https://github.com/PNX89/QUAYZ" \
      org.opencontainers.image.description="kind, kubectl, helm and terraform at the versions this repository's evidence was produced with" \
      org.opencontainers.image.licenses="MIT"
