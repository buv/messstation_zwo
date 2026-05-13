FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download
COPY *.go ./
ENV CGO_ENABLED=0 GOOS=linux GOARCH=arm64
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go build -ldflags="-s -w" -trimpath -o /out/dfld-status

# Scratch stage as a thin "delivery" image; mostly we just want the
# binary on disk via `docker create + cp`. Build-Skript erledigt das.
FROM scratch AS dist
COPY --from=build /out/dfld-status /dfld-status
