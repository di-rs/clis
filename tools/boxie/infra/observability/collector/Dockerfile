# Vector 0.59 requires journalctl >=258 to resume across host boots.
FROM docker.io/timberio/vector@sha256:98e7b4dfe50750e61470697bbd2fbeca643d12bea97f351005038ef506eab630 AS vector
FROM docker.io/library/ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd ca-certificates zlib1g libgcc-s1 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/vector \
    && chown 65532:65532 /var/lib/vector
COPY --from=vector /usr/bin/vector /usr/local/bin/vector
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/vector"]
CMD ["--config", "/etc/vector/vector.yaml"]
