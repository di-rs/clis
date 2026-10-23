# CLI schema

`cli-schema` exports a Sanctuary CLI's public Clap interface for the playground.
Call `cli_schema::emit::<Args>()` at the start of `main`, returning its exit status
when present. Argument-insensitive utilities use `emit_empty(name, description)`.

Run a built binary with `SANCTUARY_CLI_SCHEMA=1` and exactly `--help` to emit a
version 1 JSON document to stdout. Export succeeds with status 0; output errors
return status 1. Other invocations follow the normal CLI path. Export happens
before logging, input processing, or filesystem work.

The document contains the command tree, public aliases, descriptions, usage,
arguments, defaults, enum choices, value counts, and global argument ownership.
Short and full descriptions are separate (`about`/`long_about`, `help`/`long_help`).
Each argument retains its Clap ID, short/long spellings, public aliases, position,
requiredness, action and value labels. `owner` is the defining command path for a
global option, with an empty path for the root. `usage` is rendered for display;
`usage_override` distinguishes an explicitly authored usage string from generated
layout. `num_args.max: null` means unbounded arity.
Hidden entries and ambient environment values are omitted. This is a description
of the declared parser: it cannot describe arbitrary custom validation or changes
to runtime behavior. Consumers must validate documents from untrusted binaries.
