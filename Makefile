PREFIX ?= $(HOME)/.local
INSTALL_TARGET_DIR ?= $(CURDIR)/target
INSTALL_TARGET ?= $(shell rustc -vV | sed -n 's/^host: //p')

.PHONY: install-local install-rust install-python
install-local: install-rust install-python

install-rust:
	cargo build --target "$(INSTALL_TARGET)" --target-dir "$(INSTALL_TARGET_DIR)" --locked --release -p biggie -p parsu -p kara -p shish
	install -d "$(PREFIX)/bin"
	install -m 755 "$(INSTALL_TARGET_DIR)/$(INSTALL_TARGET)/release/biggie" "$(INSTALL_TARGET_DIR)/$(INSTALL_TARGET)/release/parsu" "$(INSTALL_TARGET_DIR)/$(INSTALL_TARGET)/release/kara" "$(INSTALL_TARGET_DIR)/$(INSTALL_TARGET)/release/shish" "$(PREFIX)/bin/"

install-python:
	UV_TOOL_BIN_DIR="$(PREFIX)/bin" uv tool install --python 3.15 --force "$(CURDIR)/tools/cli-check"
	UV_TOOL_BIN_DIR="$(PREFIX)/bin" uv tool install --python 3.15 --force --with "$(CURDIR)/tools/cli-check" "$(CURDIR)/tools/cli-bench"
