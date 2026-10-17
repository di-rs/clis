# Generic Boxie fixture: no Playground code, fixtures or entrypoint.
FROM ubuntu:24.04
USER 65534:65534
WORKDIR /workspace
CMD ["/bin/sh"]
