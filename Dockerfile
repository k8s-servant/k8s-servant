# k8s-servant image: the Python agent plus a pinned kubectl (the fallback tool).
#
# Two stages: the builder installs the package and downloads
# kubectl; the runtime stage copies only the results, so no compiler, pip cache or
# download tool ends up in the image that runs in the cluster.
#
# The container runs as UID 1000 with a read-only root filesystem (the Helm chart
# requires both); the only writable paths are /tmp (emptyDir) and /data (PVC).
# kubernetes-mcp-server is NOT in this image: it runs as a sidecar from its own,
# mirrored image.

# ========================
# Builder stage
# ========================
FROM python:3.14-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# kubectl supports one minor version of skew against the API server, and the guard's
# flag allow-list was checked against this version. Override with --build-arg for
# clusters more than one minor version away.
ARG KUBECTL_VERSION=v1.35.9
# Set by BuildKit from --platform (amd64, arm64); no default so multi-arch builds work.
ARG TARGETARCH

RUN apk add --no-cache curl

# Download kubectl and refuse to continue unless its published checksum matches.
WORKDIR /tmp/kubectl
RUN curl -fsSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" && \
    curl -fsSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" && \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c - && \
    install -m 0755 kubectl /usr/local/bin/kubectl

# Install the package and its dependencies into /install (not editable, no tests or
# docs): the runtime stage copies this one folder.
WORKDIR /build
COPY pyproject.toml README.pypi.md LICENSE NOTICE ./
COPY k8s_servant ./k8s_servant
COPY charts/k8s-servant/knowledge ./charts/k8s-servant/knowledge
RUN pip install --no-cache-dir --prefix=/install '.[azure]'

# ========================
# Runtime stage
# ========================
FROM python:3.14-alpine

LABEL org.opencontainers.image.title="k8s-servant" \
      org.opencontainers.image.description="Read-only AI SRE agent for Kubernetes" \
      org.opencontainers.image.source="https://github.com/k8s-servant/k8s-servant" \
      org.opencontainers.image.licenses="Apache-2.0"

# No .pyc files (the root filesystem is read-only), unbuffered logs, and HOME on the
# writable /tmp because kubectl writes its discovery cache under $HOME/.kube.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# CA certificates for HTTPS to the model endpoint, Entra ID and the Teams webhook.
RUN apk add --no-cache ca-certificates

COPY --from=builder /install /usr/local

# pip is not needed at runtime and vendors its own (often outdated) libraries, so drop
# it and the bundled wheels: a smaller image and fewer findings in vulnerability scans.
RUN python -m pip uninstall -y pip && \
    rm -rf /usr/local/lib/python3*/ensurepip
COPY --from=builder /usr/local/bin/kubectl /usr/local/bin/kubectl

# Fixed numeric UID so Kubernetes can verify runAsNonRoot without reading /etc/passwd.
RUN addgroup -S -g 1000 appgroup && adduser -S -u 1000 -G appgroup sreagent
USER 1000
WORKDIR /app

# `k8s-servant serve` is the in-cluster mode: scheduled checks and the morning report.
# `kubectl exec ... -- k8s-servant ask "..."` uses the same entry point.
ENTRYPOINT ["k8s-servant"]
CMD ["serve"]
