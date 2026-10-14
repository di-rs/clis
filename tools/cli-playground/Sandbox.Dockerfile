# Match the Ubuntu 24.04 GitHub runner that builds the native Linux binaries.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils findutils grep sed gawk less libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*
COPY tools/cli-playground/fixtures/ /opt/fixtures/
COPY tools/cli-playground/shell.sh /opt/playground/shell.sh
RUN chmod 0555 /opt/playground/shell.sh
USER 65534:65534
WORKDIR /workspace
ENTRYPOINT ["/opt/playground/shell.sh"]
