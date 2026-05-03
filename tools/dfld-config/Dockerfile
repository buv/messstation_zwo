FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends whiptail yq locales-all \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=de_DE.UTF-8 LC_ALL=de_DE.UTF-8 LANGUAGE=de_DE:de TERM=xterm-256color
COPY dfld-config.sh /usr/local/bin/dfld-config
RUN chmod +x /usr/local/bin/dfld-config
# WORKDIR=/tmp damit Default-Pfad "./dfld.yml" im Container nach
# /tmp/dfld.yml geht — und damit aus dem Bind-Mount in run.sh sichtbar wird.
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/dfld-config"]
