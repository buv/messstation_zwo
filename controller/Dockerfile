# Go-Toolchain als separate Stage holen — bookworm-slim hat nur 1.19,
# unser dfld-status braucht 1.24. Multi-Stage-COPY ist kleiner als
# apt-Backports-Pinning, ueberlebt OS-Updates ohne Versions-Drift.
FROM golang:1.24-bookworm AS go-toolchain

FROM debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    GOPATH=/root/go \
    PATH=/opt/venv/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Go-Toolchain fuer den dfld-status-Build (linux/arm64-Cross-Compile)
COPY --from=go-toolchain /usr/local/go /usr/local/go

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3=3.11.2-1+b1 \
      python3-venv=3.11.2-1+b1 \
      python3-pip=23.0.1+dfsg-1 \
      openssh-client=1:9.2p1-2+deb12u9 \
      sshpass=1.09-1+b1 \
      ca-certificates=20230311+deb12u1 \
      git=1:2.39.5-0+deb12u3 \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
 && pip install --no-cache-dir \
      "ansible-core==2.16.18" \
      "cffi==2.0.0" \
      "cryptography==46.0.7" \
      "Jinja2==3.1.6" \
      "MarkupSafe==3.0.3" \
      "packaging==26.1" \
      "pycparser==3.0" \
      "PyYAML==6.0.3" \
      "resolvelib==1.0.1" \
      "ansible==9.5.1"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /repo
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
