# Shish architecture

Shish is the shell application inside Sanctuary. It runs with the caller's
privileges; sandbox isolation and recovery belong to Boxie.

The CLI selects command-string, script, stdin, or interactive execution. A private
syntax module handles `-n` without executing commands. The runtime adapter uses
Brush for parsing, expansion, command lookup, shell state, execution, and terminal
editing. Keeping that boundary in one package avoids a second evaluator or an API
that no other component needs yet.

Stdin is read without consuming data intended for `read` or external programs.
Interactive input uses a terminal editor. All execution paths preserve command
statuses and delegate shell exit handling to the runtime.

Dependencies use published releases recorded in the workspace lockfile. Known
limitations, evidence, and follow-up acceptance criteria live in the
[compatibility gaps](compatibility-gaps.md). See the [README](../README.md) for
invocation, startup behavior, and the current compatibility promise.

## Origin

The initial shell was imported from
[di-rs/shish](https://github.com/di-rs/shish/tree/46c41468d4105a238c439aa68b4ff0e3248a964e).
Its original evaluator and lookup of executables in repository build directories
have been replaced.
