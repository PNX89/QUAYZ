# EVERY BASE IS PINNED BY DIGEST, NOT BY TAG, AND THAT IS THE POINT OF THIS FILE.
#
# A tag is a name somebody else can repoint. `golang:1.27-alpine` today and `golang:1.27-alpine`
# next month can be different bytes with the same name, so a build that is byte-identical to
# yesterday's is a coincidence rather than a property. A digest is the content, and it cannot be
# repointed by anyone.
#
# The tag is kept in a comment beside each digest, because a digest alone tells a reader nothing
# about what they are looking at, and a reviewer should not have to resolve it to find out.
#
# Refresh with:  docker pull <tag> && docker image inspect <tag> --format '{{index .RepoDigests 0}}'

# golang:1.27-alpine
FROM golang@sha256:4c9fe60190a2a3350ddc51de80d0224b8a6698d12bdfc999fee45ea9d6c46dbc AS build

WORKDIR /src

# Lockfile first, so a source change does not invalidate the dependency layer. Layer ordering is
# not only a speed decision here: a rebuild that redownloads the module cache is a rebuild whose
# inputs came off the network again, and reproducibility starts with fewer of those.
COPY go.mod go.sum ./
RUN go mod download

COPY . .

# CGO off and the build stamps stripped. -trimpath removes the build machine's directory layout
# from the binary, and without it two builds from two checkouts differ for a reason that has
# nothing to do with the code.
ENV CGO_ENABLED=0
RUN go build -trimpath -ldflags="-s -w -buildid=" -o /out/quayz-watch ./cmd/quayz-watch

# gcr.io/distroless/static:nonroot
FROM gcr.io/distroless/static@sha256:1c2c046bc09ed40fad370b599a0b1ae7987f55b01e247cf27a7c27cd97e5bbc7

COPY --from=build /out/quayz-watch /quayz-watch
USER nonroot:nonroot
ENTRYPOINT ["/quayz-watch"]
