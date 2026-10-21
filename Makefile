PREFIX ?= $(HOME)/.local

.PHONY: install-local install-rust install-python
install-local: install-rust install-python

install-rust:
	cargo build --locked --release -p biggie -p parsu -p kara
	install -d "$(PREFIX)/bin"
	install -m 755 target/release/biggie target/release/parsu target/release/kara "$(PREFIX)/bin/"

install-python:
	UV_TOOL_BIN_DIR="$(PREFIX)/bin" uv tool install --python 3.15 --force "$(CURDIR)/tools/cli-check"
	UV_TOOL_BIN_DIR="$(PREFIX)/bin" uv tool install --python 3.15 --force --with "$(CURDIR)/tools/cli-check" "$(CURDIR)/tools/cli-bench"
