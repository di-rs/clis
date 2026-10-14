# Optional local builder; PR CI builds directly on Ubuntu 24.04.
FROM rust:slim-bookworm AS build
WORKDIR /src
COPY rust-toolchain.toml ./
RUN rustup show
COPY . .
ARG CLI=echor
RUN cargo build --locked --release --package "$CLI" \
    && mkdir /out && cp "target/release/$CLI" "/out/$CLI"
FROM scratch
COPY --from=build /out/ /
