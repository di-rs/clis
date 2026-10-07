# CLI Web Preview POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Parallel agent execution requires separate authorization.

**Goal:** Build a small local browser playground that executes the repository's working Wasm CLIs, shares files between commands, and visibly reports unsupported utilities.

**Architecture:** Compile the existing Rust CLI source to Wasm and load local modules into a browser sandbox. Start with Wasmer's JavaScript SDK, a WASIX shell, and xterm.js; verify the repository binaries before choosing the final runtime. Keep generated artifacts separate from the reusable POC harness.

**Tech Stack:** Rust nightly-2026-10-04, wasm32-wasip1, Wasmer SDK 0.19.1, xterm.js 6.0.0, Python standard library for preparation and local serving. wasm32-wasip2 is a secondary experiment if the first candidate exposes material limitations.

**Spec:** The accepted brief below records the discussion and controls this POC. No separate design document was approved.

## Global Constraints

- Execute the same CLI source and domain operations as native builds. Adapt filesystem or OS interfaces only when necessary; do not replace utilities with JavaScript mock implementations.
- Inventory and attempt every utility. Unsupported utilities remain visible with a reason and a follow-up; their binaries are excluded from the runnable set.
- Support pipes, redirection, `cd`, and files shared by successive commands.
- Use a session-scoped writable virtual filesystem with seeded examples and reset. Persistence is optional.
- Start locally. Hosting, CI integration, PR comments, and full option-diff presentation are later stages.
- Do not fix Kara or other portability blockers incidentally. Do not use a Linux emulator fallback.
- Read repository guidance and affected utility contracts before changes. Preserve GNU meanings, native behavior, licensing notices, and public Rust APIs.
- A successful build does not establish runtime correctness, browser support, GNU parity, or performance.

## Review Focus

- Shell builtins can hide the compiled `true` and `false` executables; direct smoke tests must select the actual package command.
- A failed build can leave stale Wasm files; accept only artifacts emitted by the current successful build.
- Pipes must deliver EOF and retain stderr and exit status; test an empty stream, errors, and nonzero exits.
- Filesystem reset must discard writes without mixing sessions; test nested paths and filenames containing spaces.
- Browser worker loading and isolation headers can fail independently of compilation; record exact browsers tested and visible startup errors.

## Accepted Brief

The eventual product gives reviewers a link in a PR to an environment containing the CLI version from that exact source revision. A single workspace can contain multiple tools and offer direct links to a selected utility. Its terminal runs real commands against a virtual filesystem.

The eventual option catalog should show existing, added, changed, and removed arguments from Clap metadata. Changes behind an unchanged flag also need contributor notes. Clap is not universal here: Kara parses arguments manually, and `true`/`false` do not provide a Clap catalog. Plan an explicit fallback rather than assuming every utility exports metadata.

The immediate deliverable is a small POC and a compatibility report. The user's latest decision allows partial support: compile and test everything, run supported tools, and flag the rest for future work. Several controlled runtime POCs are acceptable if needed to choose a working option. Do not build the full platform now.

## Completed Exploration and Evidence

Baseline source revision: `d94a98a10883bbfeddd07fdff827bd97f7309f05`.
Compiler: `rustc 1.101.0-nightly (db8f076d2 2026-10-03)`, host `aarch64-apple-darwin`, LLVM 23.1.1. Default Cargo features were used.

The workspace has 20 binaries: `biggie`, `calr`, `catr`, `commr`, `cutr`, `echor`, `false`, `findr`, `grepr`, `headr`, `kara`, `lsr`, `mkdirr`, `parsu`, `pwdr`, `tailr`, `touchr`, `true`, `uniqr`, and `wcr`.

Both `wasm32-wasip1` and `wasm32-wasip2` compiled 16 binaries. The four blocked binaries are:

| Binary | Observed blocker | Follow-up constraint |
| --- | --- | --- |
| biggie | Unix `MetadataExt` device/inode checks in `biggie/src/output.rs` | Preserve output/input alias protection; do not remove it to compile. |
| lsr | Unix metadata and `uzers` dependency | Define portable metadata/user-name behavior before adaptation. |
| grepr | `ctrlc` dependency has no supported implementation for these targets | Preserve native signal behavior; investigate browser cancellation separately. |
| kara | `crossterm` raw terminal/event dependencies | Leave the utility flagged until a scoped interactive portability task. |

Commands already run:

```sh
cargo metadata --locked --offline --no-deps --format-version 1
cargo build --workspace --bins --keep-going --locked --offline --target wasm32-wasip1 --target wasm32-wasip2 --message-format=json
cargo build --locked --offline --release --workspace --bins --exclude biggie --exclude grepr --exclude kara --exclude lsr --target wasm32-wasip1
cargo build --locked --offline --workspace --bins --exclude biggie --exclude grepr --exclude kara --exclude lsr
```

The matrix command exited 101 because of the listed blockers. The release and native builds exited 0. Successful release Wasm files total approximately 9.28 MB; the largest is `findr`, 1,783,770 bytes. Complete runtime, SDK, and shell payload size has not been measured.

Wasmer's vendor shell demo was manually exercised with directory changes, file writes, pipes, redirection, reads, and word counting. This validates the vendor demo only. **No repository binary has been executed in a browser yet. No POC page or smoke harness exists.**

Local scratch evidence is preserved under `target/preview-poc/` in the main checkout. It includes `results.json`, matrix JSONL and stderr, successful build logs, and pinned npm dependencies. Historical logs contain paths from the former worktree. Preserved release modules are in `target/preview-poc/artifacts/wasm32-wasip1/`; build caches can be regenerated. These ignored files are local evidence, not GitHub deliverables.

Installed scratch dependencies: `@wasmer/sdk` 0.19.1, `@xterm/xterm` 6.0.0, `@xterm/addon-fit` 0.11.0, and Vite 8.3.3. Vite was installed for exploration; its use is not required. Inspect the pinned SDK declarations instead of assuming an older API.

## File Map for the Next Agent

Create a small reproducible harness under `tools/preview-poc/`:

- `README.md`: local command, requirements, limits, and measured compatibility.
- `package.json` and `package-lock.json`: exact browser dependencies and scripts.
- `prepare.py`: Cargo inventory, builds, artifact collection, and manifest generation.
- `serve.py`: localhost static serving with isolation headers.
- `cases.json`: deterministic per-utility scenarios and expected observable outcomes.
- `web/index.html`, `web/app.js`, `web/styles.css`: terminal, utility inventory, reset, and errors.
- `web/runtime.js`: package loading, sandbox lifecycle, execution, and filesystem operations.
- `web/smoke.js`: browser execution of scenarios and downloadable results.

Generate site assets and reports under `target/preview-poc/site/` and `target/preview-poc/reports/`. Keep utility source changes out of the first POC. Add a focused regression test only if a source behavior change becomes explicitly scoped.

## Task 1: Reproducible Build Inventory

**Files:** `tools/preview-poc/prepare.py`, `package.json`, `package-lock.json`, `README.md`.

**Interfaces:** `prepare.py` writes `target/preview-poc/site/manifest.json` with schema version, source SHA, dirty-state marker, toolchain, target, profile, feature selection, runtime versions, and one entry per binary. Each entry records build status, module URL when successful, and a diagnostic when blocked. Runtime results use separate fields.

- [ ] Inventory binary targets using Cargo metadata; do not hard-code a list that silently omits new utilities.
- [ ] Reuse preserved results only while source, lockfile, toolchain, features, and target match. Otherwise attempt each binary and record its actual result.
- [ ] Collect only current successful compiler artifacts into a fresh generated output directory. Prove a failed build cannot publish an older module by running with a deliberately stale file in a disposable output directory.
- [ ] Preserve the four known blockers in the report. Separate expected utility failures from infrastructure failures; an unavailable compiler or dependency must fail preparation visibly.
- [ ] Pin dependencies and preserve license notices. Provide `python3 tools/preview-poc/prepare.py` as the local preparation command.

## Task 2: Execute Local Modules in a Browser

**Files:** `web/runtime.js`, `web/index.html`, `web/app.js`, `serve.py`.

**Interfaces:** `createSession(manifest, seedFiles)` returns a session exposing `run(binary, args, stdin)`, `shell(script)`, `readFile(path)`, and `close()`. Direct execution returns exit code, stdout/stderr bytes, termination reason, and truncation state. Shell execution uses the same sandbox and files.

- [ ] Inspect the installed SDK API. `new Wasmer(...)`, `ready()`, `packages.create({modules, commands})`, and `sandboxes.create(...)` support locally compiled named modules without uploading them to a registry.
- [ ] Load one release module and execute its package command. Confirm stdout and exit status before adding the remaining supported modules.
- [ ] Add a pinned shell package after verifying its version and license. The vendor demo used `wasmer/bash@=1.0.25`; verify availability and compatibility rather than assuming it is the final choice.
- [ ] Serve SDK workers and Wasm assets from consistent relative locations. Preserve the SDK asset tree if using static imports; validate bundler worker handling if using Vite.
- [ ] Serve localhost with `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`. Show a useful error when required browser capabilities or assets are unavailable.
- [ ] Disable sandbox networking, seed `/workspace`, and set execution time/output limits. Preserve diagnostic information when limits terminate a command.
- [ ] Connect xterm.js to the interactive shell, including stdin, stdout, stderr, terminal resize, EOF, and cancellation. Verify actual repository command resolution in the shell.

## Task 3: Test Every Compiled Utility and Shared Files

**Files:** `cases.json`, `web/smoke.js`, `web/runtime.js`.

**Interfaces:** Smoke results identify binary, case, source SHA, runtime/browser identity, exit status, byte-output comparisons, filesystem observations, and pass/fail/unverified state. Compilation status remains distinct.

- [ ] Define deterministic scenarios from each utility's README, implementation contract, and existing tests. Capture native outputs from the same source revision as an independent runtime comparison; this does not establish GNU parity.
- [ ] Exercise all 16 compiled binaries. Include deterministic calendar input, text and binary file reads, stdin EOF, sorted comparison inputs, XML input, directory creation, file creation, and cwd reporting.
- [ ] Select compiled `true`/`false` by package command; assert exit 0/1 respectively. A shell builtin must not satisfy these cases.
- [ ] Test the supported pipeline `catr sample.txt | headr -n 1 > first.txt`, then read `first.txt` in a later command. Test `cd`, nested paths, and a filename containing spaces.
- [ ] Test empty stdin, missing files, stderr, nonzero status, and output limits. Compare exact observable bytes where the contract allows; document environment-dependent fields instead of broadly normalizing failures away.
- [ ] Reset the session and verify writes disappear while seeds return. Verify a second session cannot see the first session's writes.
- [ ] Record unsupported runtime capabilities precisely. SDK filesystem stat exposes kind/size; file existence alone does not validate `touchr` timestamp behavior. Mark timestamp semantics unverified unless independently observed in the guest.

## Task 4: Minimal Review Interface and Compatibility Report

**Files:** `web/app.js`, `web/styles.css`, `README.md`.

- [ ] Display all discovered utilities with build, load, and smoke status. Blocked utilities show the diagnostic and follow-up rather than disappearing.
- [ ] Show source revision, selected tool, terminal, seeded files, and reset. Provide actual `--help` output where supported, with an explicit fallback for tools without it.
- [ ] Test available Chrome, Firefox, and Safari implementations and record exact versions. Mark unavailable browsers untested; do not claim universal support.
- [ ] Measure initial download size and startup time separately from CLI execution time. Do not present these as utility benchmarks.
- [ ] If Wasmer cannot execute a meaningful supported workload, create a second isolated POC using WASIp2/Jco or a WASI runner and the same scenarios. Compare shell, shared filesystem, cancellation, browser coverage, payload, and licensing before choosing. Do not rewrite a shell merely to make a candidate appear viable.
- [ ] Document the working subset and remaining failures. Finish with one local command sequence: preparation followed by `python3 tools/preview-poc/serve.py`, printing the localhost URL.

## Hosting and Contributor Workflow After the POC

The reusable output is a static site bundle containing its immutable source manifest and modules. A contributor can prepare locally, upload that bundle, and attach its deployment URL to a PR. A future automation can run the same preparation command and publish without redesigning the runtime.

Cloudflare Pages Direct Upload is a candidate for a manual-first workflow. Wrangler accepts a prepared directory, branch label, and commit hash. Unique deployment URLs identify a revision; a branch alias can represent the newest preview. A Direct Upload project cannot later switch to built-in Git integration, although GitHub Actions can continue deploying with Wrangler. Choose the project mode before provisioning. Its 25 MiB per-file limit fits the measured release CLI modules; the entire SDK/shell bundle still needs measurement.

Netlify CLI draft deployments are another static-hosting candidate with unique URLs and configurable headers. Review retention and pricing when selecting an account. GitHub Actions artifacts alone do not serve an interactive website.

Reviewer files remain in memory for the POC. Later, IndexedDB or OPFS could retain files per browser origin; this does not provide shared storage and preview origins affect continuity. Hosting artifact retention is a separate decision from filesystem persistence.

For later PR automation, build untrusted fork code without deployment secrets. A trusted publisher should validate and publish static artifacts without executing PR-provided scripts. Avoid running untrusted PR code with `pull_request_target` credentials. Add one workspace URL to deployment status or PR content, plus optional tool-specific links. Use exact SHA manifests and clearly label a movable latest alias.

Later option presentation should compare normalized Clap metadata from the PR base and head, identify added/removed/changed options, and retain contributor notes for behavior changes that metadata cannot describe. Shared dependency changes may affect several utilities; determine affected packages explicitly. None of this is required to validate the local runtime.

## Sources and Technical Caveats

- [Wasmer JavaScript runtime](https://docs.wasmer.io/runtime/js/) and [WASIX](https://docs.wasmer.io/runtime/wasix/): first candidate for browser processes, shell, and filesystem support.
- [WASIX patched Rust repositories](https://www.wasix.org/docs/language-guide/rust/patched-repos/): using a WASIX toolchain can alter dependency configuration; isolate any later experiment and record source/dependency differences.
- [Rust WASIp1 target](https://doc.rust-lang.org/rustc/platform-support/wasm32-wasip1.html) and [WASIp2 target](https://doc.rust-lang.org/rustc/platform-support/wasm32-wasip2.html).
- [Jco browser shim](https://github.com/bytecodealliance/jco/blob/main/packages/preview2-shim/README.md), [browser_wasi_shim](https://github.com/bjorn3/browser_wasi_shim), and [Runno WASI runner](https://github.com/taybenlor/runno/tree/main/packages/wasi): alternatives require capability and filesystem checks; browser support is not interchangeable.
- [Pages Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/), [preview deployments](https://developers.cloudflare.com/pages/configuration/preview-deployments/), [headers](https://developers.cloudflare.com/pages/configuration/headers/), and [limits](https://developers.cloudflare.com/pages/platform/limits/).
- [Netlify CLI](https://docs.netlify.com/api-and-cli-guides/cli-guides/get-started-with-cli/) and [headers](https://docs.netlify.com/manage/routing/headers/).
- [GitHub pull_request_target security](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).
- [Origin private filesystem](https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system).

The installed Wasmer SDK license is Modified MIT with a Wasmer display condition for qualifying commercial products. Review its exact license and shell notices before product distribution; do not describe the dependency as unrestricted MIT.

## Completion and Delivery

- [ ] Review final diff and run checks selected from the repository CI workflow. Source edits require affected Rust verification; documentation-only work does not require rebuilding Rust.
- [ ] Publish the POC report with actual commands, browser identities, accepted gaps, and licensing findings. Keep failures visible.
- [ ] Deliver the local URL and reproducible commands. Hosting, credentials, CI publication, and full PR UI remain follow-up work until separately scoped.

This handoff itself changes documentation only. Local `lychee` and `typos` were unavailable during preparation; report that limitation rather than claiming the complete documentation CI passed.
