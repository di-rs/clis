# Shish compatibility and delivery inventory

Shish targets correct common shell operations with explicit recovery boundaries.
Full POSIX conformance is a future product decision, not a platform release gate.
This inventory covers shell language and shell builtins against
[POSIX.1-2024](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html).
It does not claim operating-system-wide or complete utility conformance.

## Reading the evidence

- **Verified** means the linked cases passed in the recorded environment; it does
  not mean every variation of that feature works.
- **Partial** means tested cases work but a reproduced gap remains in the area.
- **Missing** means the specified behavior was attempted and did not work.
- **Unverified** means no adequate shish evidence has been recorded. An upstream
  claim, parser acceptance, or absence of a reported failure is not verification.

The initial audit ran on 2026-10-22: shish 0.1.0 at runtime foundation `b643cdb`,
Darwin 27.0.0 arm64, Brush core 0.5.0/parser 0.4.0/builtins 0.2.0/interactive 0.4.0,
Reedline 0.47.0, default sh/POSIX mode. Commands used `-c` unless otherwise stated.
PTY cases use `-i`, a 120-column terminal, and an isolated temporary home. Linux,
gVisor, other locales, and other architectures remain unverified by this local run.
CI results must be recorded before extending the platform claim.

Independent reference checks used macOS `/bin/bash` 3.2.57(1)-release with
`--noprofile --norc --posix`, `LC_ALL=C`, `PATH=/usr/bin:/bin`, and a temporary HOME.
Bash is a comparison implementation, not the standard; its differences and
unspecified behavior do not automatically become requirements. New 2024-only
requirements need specification-derived expectations or another suitable reference.
Expected bytes are written from the specification/reference, never regenerated
from shish. M01 adds explicit `--bash` with the same dependency versions; mode-selection,
parse-only, registry, and conflict tests are in [audit.rs](../tests/audit.rs).
This does not verify the entire Bash feature set. External utility output depends
on the image's tools: Brush's default
sh registry does not include `echo` or `printf`, so those names currently use PATH.

See [audit tests](../tests/audit.rs), [invocation tests](../tests/runtime.rs), and
[utility tests](../tests/builtins.rs). The
[test guide](testing.md) explains reproducibility and the terminal driver.

## POSIX scope audit

Sections below refer to the shell language in POSIX.1-2024; named utilities also
have their own utility reference pages. Each broad area retains untested edges.

| Area | Reference | Initial evidence and remaining scope |
| --- | --- | --- |
| Quoting | §2.2 | Partial audit: quoted arguments and unterminated quotes tested; dedicated boundary tasks 13–18 remain. |
| Parameters and expansion | §2.5–2.6 | Quoted `$@`, command-substitution newline removal, arithmetic, and dollar-single-quotes manually matched reference. P03 affects `$!`; full parameter operator matrix unverified. |
| Field splitting | §2.6.5 | Partial; P01 reproduces loss of empty non-whitespace IFS fields. |
| Pathname expansion | §2.6.6 | Unverified: glob sorting, locale, dotfiles, and unmatched patterns need separate cases. |
| Lookup and statuses | §2.8, §2.9.1 | Verified missing command 127, nonexecutable 126, parse error 2, and ordinary status propagation. Function/builtin/PATH precedence and caching need broader coverage. |
| Pipelines | §2.9.2 | Last-command status manually matched; dedicated tasks 55–57 retain their own PRs. Negation and pipefail matrix unverified. |
| Redirections and here-documents | §2.7 | Verified failure has no output/file effect; quoted/unquoted here-doc delimiters. Descriptor ordering manually matched. Descriptor closing, tab stripping, noclobber, and mixed redirections unverified. |
| Assignments and special builtins | §2.9.1, §2.15 | Partial; P02 affects assignment persistence. Readonly assignment failure aborts execution, with implementation-specific nonzero status. Full special-builtin error/lookup matrix unverified. |
| Control flow | §2.9.3–2.9.4 | Verified for/case/continue sample. Nested loops, break levels, until/while, and error propagation unverified. |
| Functions | §2.9.5 | Verified argument scope in one function case; recursion, return, traps, and lookup interactions unverified. |
| Subshells/environment | §2.13 | Verified variable isolation in parentheses. File descriptors, traps, and background environment edges unverified. |
| Traps/signals | §2.12, `trap` | Partial: EXIT handler/status verified; P04 reproduces missing TERM handling. Other signal traps remain unverified. |
| Asynchronous jobs | §2.9.3, §2.11, `jobs`, `wait` | Manual background/wait completion passed; P03 breaks PID tracking. Interactive launch/output/reaping await tasks 46–54. |
| Terminal ownership | §2.11 | Verified foreground Ctrl-C returns control and status 130 in a PTY. Stop/continue, fg/bg, and terminal resize remain unverified. |
| Invocation/startup | `sh` | Verified -c/script/-s/-n arguments and EOF. P05 records intentionally skipped ENV startup. Login/interactive option combinations need more coverage. |
| Builtin requirements | §2.15 and individual utilities | true/false status/argument contract verified. Complete required-builtin option, error, and environment matrices remain unverified. |

These are bounded checks, not a conformance suite. Upstream's
[compatibility reference](https://github.com/reubeno/brush/blob/main/docs/reference/compatibility.md)
provides audit candidates, not confirmed shish defects. `select`, `wait -n`,
`disown`, advanced key bindings, and other Bash-specific gaps need reproduction
in explicit Bash mode and stay outside the POSIX gap count.

## Confirmed gaps and focused follow-ups

All P01–P04 reproducers use the version/platform/mode snapshot above. Unless noted,
stdout is shown with `\n` for a newline and stderr is empty. The expected status
is 0. The owner classification identifies where a fix belongs, not an allegation
based only on upstream documentation. No upstream issue has been filed or linked
for these reproductions yet.

### P01: non-whitespace IFS fields

Reference: POSIX §2.6.5. Reproducer:

```sh
shish -c 'IFS=:; v=":a::b:"; set -- $v; printf "<%s>\n" "$@"'
```

Expected `<>\n<a>\n<>\n<b>\n`; observed `<a>\n<b>\n`, status 0. Empty fields
are lost, corrupting delimiter-based records. Owner: Brush expansion. Workaround:
keep values quoted and use an explicit data parser when delimiters matter.
Acceptance test: `posix_ifs_preserves_empty_fields` in [audit.rs](../tests/audit.rs),
currently ignored because it fails. Proposed separate PR P01: reproduce in Brush,
fix field splitting upstream, adopt a published release, and enable the test.

### P02: assignments before special builtins

Reference: POSIX §2.9.1 and §2.15. Reproducer:

```sh
shish -c 'unset v; v=kept :; printf "<%s>\n" "$v"'
```

Expected `<kept>\n`; observed `<>\n`, status 0. Subsequent commands lose assigned
shell state. Owner: Brush evaluation. Workaround: `v=kept; :` when that separate
assignment has the intended semantics. Acceptance test:
`posix_special_builtin_assignment_persists` in [audit.rs](../tests/audit.rs),
currently ignored. Proposed separate PR P02: upstream special-builtin assignment
semantics, published dependency update, and assignment/error regression matrix.

### P03: asynchronous process parameter

Reference: POSIX §2.5.2 and §2.9.3. Reproducer:

```sh
shish -c '/bin/sleep 0.01 & printf "<%s>\n" "${!:+set}"; wait'
```

Expected `<set>\n`; observed `<>\n`, status 0. PID-based wait, cancellation, and
ownership scripts cannot identify the launched process through `$!`. Owner:
Brush job tracking; jobs currently use internal asynchronous tasks without a
reliable process ID. Workaround: `wait` without a PID when waiting for all jobs is
acceptable; do not use this as a durable-job identity. Acceptance test:
`posix_background_process_parameter_is_set` in [audit.rs](../tests/audit.rs),
currently ignored. Proposed separate PR P03: upstream job/process identity and
published dependency update. Screenshot jobs tasks remain separate.

### P04: signal traps

Reference: POSIX §2.12 and `trap`. Reproducer:

```sh
shish -c 'trap "printf caught" TERM; kill -TERM $$; printf done'
```

Expected `caughtdone`, status 0; observed no output and termination by SIGTERM
(signal 15, conventionally status 143 when observed by a parent shell). Owner:
Brush signal/trap dispatch; handler registration does not establish TERM delivery.
EXIT handling works separately. Workaround: delegate signal-sensitive scripts to
a shell with verified handling; do not depend on this trap for cleanup.
Acceptance test: `posix_term_trap_handles_signal` in [audit.rs](../tests/audit.rs),
currently ignored. Proposed separate PR P04: upstream signal delivery, handler
execution and inheritance, published update, and PTY/noninteractive regressions.

### P05: interactive startup files are skipped

Reference: the POSIX `sh` ENV startup requirements. In the snapshot above, launch
`ENV=/absolute/startup shish -i` with a file containing `export MARK=loaded`, then
inspect `$MARK`. POSIX interactive startup would source the file; shish leaves
MARK unset. Owner: intentional shish integration policy (`ProfileLoadBehavior`
and `RcLoadBehavior` are Skip), verified by the PTY test
`startup_policy_skips_interactive_env` in [audit.rs](../tests/audit.rs).
Impact: users must source configuration explicitly. Workaround: `. /absolute/startup`.
Proposed separate PR P05, if strict startup compatibility is selected: implement
and test ENV expansion, privilege checks, interaction modes, and file errors.
Do not silently enable host startup files as an unrelated feature fix.

### P06: command descriptions omit functions

Reference: POSIX `command`, `-v` description behavior. Reproduced during task 06
with the same shish/Brush versions, platform, and default mode as the initial audit:

```sh
shish -c 'f() { :; }; command -v f'
```

Expected `f\n`, status 0; observed empty output, status 1, empty stderr. Scripts
that use `command -v` to detect a function incorrectly conclude it is absent.
Owner: Brush's `command` builtin lookup only considers builtins and executables.
Workaround: use `type` for inspection where its descriptive format is acceptable.
Acceptance test: `posix_command_v_reports_functions` in [audit.rs](../tests/audit.rs),
currently ignored. Proposed separate PR P06: upstream function/alias description
lookup, published dependency update, and acceptance tests. No upstream issue linked yet.

## Foundation stack

Each row is independently reviewable and targets its predecessor. No PR is merged
automatically. Feature tasks below build on F08, prioritizing common execution
before interactive enhancements. M01 adds opt-in `--bash` before Bash-specific
features; it does not change the default mode.

| Stage | PR | Deliverable |
| --- | --- | --- |
| F00 | [#95](https://github.com/di-rs/sanctuary/pull/95) | Platform compatibility and durability policy |
| F01 | [#96](https://github.com/di-rs/sanctuary/pull/96) | Package import, workspace integration, smoke coverage |
| F02 | [#97](https://github.com/di-rs/sanctuary/pull/97) | EOF, input lifecycle, failure handling |
| F03 | [#98](https://github.com/di-rs/sanctuary/pull/98) | Reusable true/false libraries |
| F04 | [#99](https://github.com/di-rs/sanctuary/pull/99) | Private Brush parsing facade |
| F05 | [#100](https://github.com/di-rs/sanctuary/pull/100) | Brush runtime and invocation modes |
| F06 | [#101](https://github.com/di-rs/sanctuary/pull/101) | Sanctuary builtin integration and installation |
| F07 | [#102](https://github.com/di-rs/sanctuary/pull/102) | Temporal/Boxie research and recovery design |
| F08 | [#103](https://github.com/di-rs/sanctuary/pull/103) | Compatibility inventory and CLI/PTY infrastructure |
| M01 | [#105](https://github.com/di-rs/sanctuary/pull/105) | Explicit opt-in Bash mode |

## Screenshot task inventory

Each numbered item keeps its own PR, including already-working behavior: that PR
adds focused regression evidence and updates its row. Pending rows are unverified,
not promises inferred from Brush. Bash mode is required for `complete`, `history`,
and `declare`; their absence from default mode is a documented mode choice.
Completion UI and prompt appearance are product behavior rather than POSIX gates.
The task numbers are stable even when implementation order changes.

| Task | Area and behavior | Mode/reference | Status and evidence | Focused PR |
| --- | --- | --- | --- | --- |
| 01 | Basics: prompt | sh; invocation/§2.9.1 | Verified: home/directory prompt, PS1, no noninteractive prompt; [task_01](../tests/audit.rs) | [01 PR](https://github.com/di-rs/sanctuary/pull/106) |
| 02 | Basics: invalid commands | sh; invocation/§2.9.1 | Verified: status 127, diagnostic, and continued stdin execution; [task_02](../tests/audit.rs) | [02 PR](https://github.com/di-rs/sanctuary/pull/107) |
| 03 | Basics: REPL | sh; invocation/§2.9.1 | Verified: multiline interactive loop and persistent variables; [task_03](../tests/audit.rs) | [03 PR](https://github.com/di-rs/sanctuary/pull/108) |
| 04 | Basics: exit | sh; invocation/§2.9.1 | Verified: exit stops later commands and inherits the last status; [task_04](../tests/audit.rs) | [04 PR](https://github.com/di-rs/sanctuary/pull/109) |
| 05 | Basics: echo | sh; invocation/§2.9.1 | Verified: ordinary echo in sh/PATH and Bash builtin modes; option extensions excluded; [task_05](../tests/audit.rs) | [05 PR](https://github.com/di-rs/sanctuary/pull/110) |
| 06 | Basics: type | sh; invocation/§2.9.1 | Verified: builtin/external type and function type via -t extension; missing type diagnostic. P06 tracks command -v separately; [task_06](../tests/audit.rs) | [06 PR](https://github.com/di-rs/sanctuary/pull/111) |
| 07 | Basics: executable lookup | sh; invocation/§2.9.1 | Verified: custom PATH, executable permission, and missing lookup; [task_07](../tests/audit.rs) | [07 PR](https://github.com/di-rs/sanctuary/pull/112) |
| 08 | Basics: program execution | sh; invocation/§2.9.1 | Verified: argv boundaries, stdout/stderr, and external status; [task_08](../tests/audit.rs) | [08 PR](https://github.com/di-rs/sanctuary/pull/113) |
| 09 | Navigation: pwd | sh; pwd/cd | Verified: initial and changed working directories; [task_09](../tests/audit.rs) | [09 PR](https://github.com/di-rs/sanctuary/pull/114) |
| 10 | Navigation: absolute cd | sh; pwd/cd | Verified: absolute cd updates shell cwd while host test cwd is unchanged; [task_10](../tests/audit.rs) | [10 PR](https://github.com/di-rs/sanctuary/pull/115) |
| 11 | Navigation: relative cd | sh; pwd/cd | Verified: relative paths and parent traversal; [task_11](../tests/audit.rs) | [11 PR](https://github.com/di-rs/sanctuary/pull/116) |
| 12 | Navigation: home-directory cd | sh; pwd/cd | Verified: cd without operands and tilde expansion; [task_12](../tests/audit.rs) | [12 PR](https://github.com/di-rs/sanctuary/pull/117) |
| 13 | Quoting: single quotes | sh; §2.2 | Verified: single quotes retain whitespace, empty words, metacharacters, and joined quote segments; [task_13](../tests/audit.rs) | [13 PR](https://github.com/di-rs/sanctuary/pull/118) |
| 14 | Quoting: double quotes | sh; §2.2 | Verified: double quotes preserve whitespace and empty expansions without globbing; [task_14](../tests/audit.rs) | [14 PR](https://github.com/di-rs/sanctuary/pull/119) |
| 15 | Quoting: unquoted backslashes | sh; §2.2 | Verified: escaped spaces and metacharacters, plus backslash-newline joining; [task_15](../tests/audit.rs) | [15 PR](https://github.com/di-rs/sanctuary/pull/120) |
| 16 | Quoting: backslashes in single quotes | sh; §2.2 | Verified: backslashes remain literal inside single quotes; [task_16](../tests/audit.rs) | [16 PR](https://github.com/di-rs/sanctuary/pull/121) |
| 17 | Quoting: backslashes in double quotes | sh; §2.2 | Verified: double-quote escape rules preserve unknown escapes and remove continued newlines; [task_17](../tests/audit.rs) | [17 PR](https://github.com/di-rs/sanctuary/pull/122) |
| 18 | Quoting: quoted executables | sh; §2.2 | Verified: executable paths containing spaces retain argument boundaries; [task_18](../tests/audit.rs) | [18 PR](https://github.com/di-rs/sanctuary/pull/123) |
| 19 | Redirection: stdout | sh; §2.7 | Verified: exact file contents, unaffected output stream, and success status; [task_19](../tests/audit.rs) | [19 PR](https://github.com/di-rs/sanctuary/pull/124) |
| 20 | Redirection: stderr | sh; §2.7 | Verified: exact file contents, unaffected output stream, and success status; [task_20](../tests/audit.rs) | [20 PR](https://github.com/di-rs/sanctuary/pull/125) |
| 21 | Redirection: append stdout | sh; §2.7 | Verified: exact file contents, unaffected output stream, and success status; [task_21](../tests/audit.rs) | [21 PR](https://github.com/di-rs/sanctuary/pull/126) |
| 22 | Redirection: append stderr | sh; §2.7 | Verified: exact file contents, unaffected output stream, and success status; [task_22](../tests/audit.rs) | [22 PR](https://github.com/di-rs/sanctuary/pull/127) |
| 23 | Command completion: builtins | interactive; product UI | Verified: bounded PTY Tab interaction with isolated PATH; [task_23](../tests/audit.rs) | [23 PR](https://github.com/di-rs/sanctuary/pull/138) |
| 24 | Command completion: arguments | interactive; product UI | Verified: bounded PTY Tab interaction with isolated PATH; [task_24](../tests/audit.rs) | [24 PR](https://github.com/di-rs/sanctuary/pull/139) |
| 25 | Command completion: no matches | interactive; product UI | Unverified; dedicated audit pending | Planned task 25 |
| 26 | Command completion: executables | interactive; product UI | Verified: custom PATH executable is completed and executed; [task_26](../tests/audit.rs) | [26 PR](https://github.com/di-rs/sanctuary/pull/140) |
| 27 | Command completion: multiple matches | interactive; product UI | Verified: completion menu lists both executable candidates and can be cancelled; [task_27](../tests/audit.rs) | [27 PR](https://github.com/di-rs/sanctuary/pull/141) |
| 28 | Command completion: partial matches | interactive; product UI | Unverified; dedicated audit pending | Planned task 28 |
| 29 | Filename completion: files | interactive; product UI | Verified: a completed filename containing a space opens the intended file; [task_29](../tests/audit.rs) | [29 PR](https://github.com/di-rs/sanctuary/pull/142) |
| 30 | Filename completion: nested files | interactive; product UI | Verified: directory continuation and a nested file are completed and opened; [task_30](../tests/audit.rs) | [30 PR](https://github.com/di-rs/sanctuary/pull/143) |
| 31 | Filename completion: directories | interactive; product UI | Verified: completion appends a directory separator and cd enters the result; [task_31](../tests/audit.rs) | [31 PR](https://github.com/di-rs/sanctuary/pull/144) |
| 32 | Filename completion: no matches | interactive; product UI | Unverified; dedicated audit pending | Planned task 32 |
| 33 | Filename completion: multiple matches | interactive; product UI | Verified: both filename candidates appear in the completion menu; [task_33](../tests/audit.rs) | Current task PR |
| 34 | Filename completion: partial matches | interactive; product UI | Unverified; dedicated audit pending | Planned task 34 |
| 35 | Filename completion: multiple arguments | interactive; product UI | Unverified; dedicated audit pending | Planned task 35 |
| 36 | Programmable completion: registration | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 36 |
| 37 | Programmable completion: missing specifications | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 37 |
| 38 | Programmable completion: display specifications | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 38 |
| 39 | Programmable completion: single candidate | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 39 |
| 40 | Programmable completion: no candidates | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 40 |
| 41 | Programmable completion: command-line arguments | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 41 |
| 42 | Programmable completion: environment variables | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 42 |
| 43 | Programmable completion: multiple candidates | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 43 |
| 44 | Programmable completion: longest common prefix | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 44 |
| 45 | Programmable completion: unregistration | bash; Bash §8.6–8.7 | Unverified; dedicated audit pending | Planned task 45 |
| 46 | Background jobs: jobs | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 46 |
| 47 | Background jobs: launch | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 47 |
| 48 | Background jobs: output | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 48 |
| 49 | Background jobs: list one | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 49 |
| 50 | Background jobs: list multiple | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 50 |
| 51 | Background jobs: reap one | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 51 |
| 52 | Background jobs: reap multiple | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 52 |
| 53 | Background jobs: reap before prompt | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 53 |
| 54 | Background jobs: recycle job numbers | interactive sh; §2.11/jobs/wait | Unverified; dedicated audit pending | Planned task 54 |
| 55 | Pipelines: two commands | sh; §2.9.2 | Verified: pipeline output, final status, and bounded fixture execution; [task_55](../tests/audit.rs) | [55 PR](https://github.com/di-rs/sanctuary/pull/128) |
| 56 | Pipelines: builtins | sh; §2.9.2 | Verified: pipeline output, final status, and bounded fixture execution; [task_56](../tests/audit.rs) | [56 PR](https://github.com/di-rs/sanctuary/pull/129) |
| 57 | Pipelines: multiple commands | sh; §2.9.2 | Verified: pipeline output, final status, and bounded fixture execution; [task_57](../tests/audit.rs) | [57 PR](https://github.com/di-rs/sanctuary/pull/130) |
| 58 | History: builtin | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 58 |
| 59 | History: listing | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 59 |
| 60 | History: entry limits | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 60 |
| 61 | History: up-arrow | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 61 |
| 62 | History: down-arrow | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 62 |
| 63 | History: recalled execution | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 63 |
| 64 | History persistence: read file | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 64 |
| 65 | History persistence: write file | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 65 |
| 66 | History persistence: append file | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 66 |
| 67 | History persistence: startup read | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 67 |
| 68 | History persistence: exit write | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 68 |
| 69 | History persistence: exit append | bash; Bash §9 | Unverified; dedicated audit pending | Planned task 69 |
| 70 | Parameters: declare | bash declare; sh §2.5–2.6 | Verified: declare creates a shell variable (bash mode); [task_70](../tests/audit.rs) | [70 PR](https://github.com/di-rs/sanctuary/pull/131) |
| 71 | Parameters: missing-variable diagnostics | bash declare; sh §2.5–2.6 | Verified: missing variable diagnostics (bash mode); [task_71](../tests/audit.rs) | [71 PR](https://github.com/di-rs/sanctuary/pull/132) |
| 72 | Parameters: storage | bash declare; sh §2.5–2.6 | Verified: shell variable storage and export (sh mode); [task_72](../tests/audit.rs) | [72 PR](https://github.com/di-rs/sanctuary/pull/133) |
| 73 | Parameters: name validation | bash declare; sh §2.5–2.6 | Verified: invalid declaration names are rejected (bash mode); [task_73](../tests/audit.rs) | [73 PR](https://github.com/di-rs/sanctuary/pull/134) |
| 74 | Parameters: expansion | bash declare; sh §2.5–2.6 | Verified: quoted and unquoted variable expansion (sh mode); [task_74](../tests/audit.rs) | [74 PR](https://github.com/di-rs/sanctuary/pull/135) |
| 75 | Parameters: braced expansion | bash declare; sh §2.5–2.6 | Verified: braced parameter boundaries and defaults (sh mode); [task_75](../tests/audit.rs) | [75 PR](https://github.com/di-rs/sanctuary/pull/136) |
| 76 | Parameters: empty expansion | bash declare; sh §2.5–2.6 | Verified: empty quoted and unquoted parameters (sh mode); [task_76](../tests/audit.rs) | [76 PR](https://github.com/di-rs/sanctuary/pull/137) |

Bash-extension references: [completion](https://www.gnu.org/software/bash/manual/html_node/Programmable-Completion.html),
[completion builtins](https://www.gnu.org/software/bash/manual/html_node/Programmable-Completion-Builtins.html),
[history](https://www.gnu.org/software/bash/manual/html_node/Bash-History-Facilities.html),
and [Bash builtins](https://www.gnu.org/software/bash/manual/html_node/Bash-Builtins.html).

For any newly found gap, retain its reference section, tested versions/platform/mode,
minimal reproducer, expected and actual status/streams/effects, impact, owner,
workaround, regression link, upstream issue when available, and separate follow-up
PR. Update evidence after implementation changes and dependency upgrades; keep
untested platforms and semantics explicit. If strict conformance is selected,
first choose its scope/platforms, then expand this matrix and promote the relevant
gaps to required acceptance criteria. They do not collectively block platform work.
