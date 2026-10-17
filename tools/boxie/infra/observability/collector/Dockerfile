# Frozen build inputs; deployment requires a separately published registry digest.
FROM docker.io/timberio/vector@sha256:98e7b4dfe50750e61470697bbd2fbeca643d12bea97f351005038ef506eab630 AS certificates
FROM scratch AS vector-amd64
ADD --checksum=sha256:a8dbc43c18ae25d0b23a712c9262f3aad904e352b1db5e7db1d9c4aecab59496 https://github.com/vectordotdev/vector/releases/download/v0.59.0/vector-0.59.0-x86_64-unknown-linux-musl.tar.gz /vector.tar.gz
FROM scratch AS vector-arm64
ADD --checksum=sha256:9a666b266168fc44a086741ed92a01880d5ae6e08e4bb49ef40a54e47cc5a8a0 https://github.com/vectordotdev/vector/releases/download/v0.59.0/vector-0.59.0-aarch64-unknown-linux-musl.tar.gz /vector.tar.gz
ARG TARGETARCH
FROM vector-${TARGETARCH} AS binary
FROM docker.io/library/ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7
ARG TARGETARCH
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /tmp/bootstrap-ca.pem
COPY --from=binary /vector.tar.gz /tmp/vector.tar.gz
COPY ${TARGETARCH}.sha256 /tmp/packages.sha256
RUN set -eu; \
    echo '714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5  /tmp/bootstrap-ca.pem' | sha256sum -c -; \
    rm -f /etc/apt/sources.list.d/ubuntu.sources /etc/apt/apt.conf.d/docker-clean; \
    printf '%s\n' \
      'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261015T000000Z resolute main' \
      'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261015T000000Z resolute-updates main' \
      'deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261015T000000Z resolute-security main' > /etc/apt/sources.list; \
    apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca.pem -o APT::Update::Error-Mode=any update; \
    apt-get -o Acquire::https::CaInfo=/tmp/bootstrap-ca.pem install --download-only --no-install-recommends -y systemd=259.5-0ubuntu3.4 ca-certificates=20260601~26.04.1; \
    cd /var/cache/apt/archives; sha256sum -c /tmp/packages.sha256; \
    test "$(find . -maxdepth 1 -name '*.deb' | wc -l)" -eq 6; \
    DEBIAN_FRONTEND=noninteractive apt-get --no-download install --no-install-recommends -y systemd=259.5-0ubuntu3.4 ca-certificates=20260601~26.04.1; \
    tar -xzf /tmp/vector.tar.gz -C /tmp; \
    cp /tmp/vector-*/bin/vector /usr/local/bin/vector; \
    mkdir -p /var/lib/vector; chown 65532:65532 /var/lib/vector; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/vector"]
CMD ["--config", "/etc/vector/vector.yaml"]
