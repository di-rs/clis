# Generic Boxie fixture: no cli-playground code, fixtures or entrypoint.
FROM ubuntu:24.04
USER 65534:65534
WORKDIR /workspace
CMD ["/bin/sh"]
