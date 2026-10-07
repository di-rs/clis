use cli_bench as bench_api;
macro_rules! require {
    ($condition:expr) => {
        if !$condition {
            return Err(format!(
                "failed condition at line {}: {}",
                line!(),
                stringify!($condition)
            )
            .into());
        }
    };
}
macro_rules! require_eq {
    ($actual:expr, $expected:expr $(,)?) => {{
        let actual = $actual;
        let expected = $expected;
        if actual != expected {
            return Err(
                format!("line {}: actual {actual:?}, expected {expected:?}", line!()).into(),
            );
        }
    }};
}
mod common;
use cli_bench::{ProcessRunner, ProcessStatus, StopReason, Stream};
use common::{TestResult, captures, command, fixture, policy};
use std::{
    fs,
    sync::atomic::Ordering,
    time::{Duration, Instant},
};

#[test]
fn direct_argv_is_literal_and_streams_preserve_binary_bytes() -> TestResult {
    let root = assert_fs::TempDir::new()?;
    let program = fixture(
        root.path(),
        "argv",
        "printf '%s\\000' \"$@\"; printf '\\377\\000err' >&2",
    )?;
    let mut spec = command(root.path(), program);
    spec.argv = [
        "",
        "a b",
        "'quoted'",
        "$(touch injected)",
        "*;|&",
        "@literal",
    ]
    .map(str::to_owned)
    .into();
    let paths = captures(root.path());
    let result = ProcessRunner::new(policy()).execute(&spec, &paths)?;
    result.check_expected(0)?;
    require_eq!(
        fs::read(paths.stdout)?,
        b"\0a b\0'quoted'\0$(touch injected)\0*;|&\0@literal\0"
    );
    require_eq!(fs::read(paths.stderr)?, b"\xff\0err");
    require!(!root.join("injected").exists());
    Ok(())
}
#[test]
fn captures_long_binary_streams_concurrently() -> TestResult {
    let root = assert_fs::TempDir::new()?;
    let program = fixture(
        root.path(),
        "binary",
        "i=0; while [ \"$i\" -lt 20000 ]; do printf '\\000\\377abc'; printf '\\376\\000xyz' >&2; i=$((i + 1)); done",
    )?;
    let paths = captures(root.path());
    let result = ProcessRunner::new(policy()).execute(&command(root.path(), program), &paths)?;
    result.check_expected(0)?;
    require_eq!(fs::read(paths.stdout)?, b"\0\xffabc".repeat(20_000));
    require_eq!(fs::read(paths.stderr)?, b"\xfe\0xyz".repeat(20_000));
    require_eq!(
        (result.stdout_bytes, result.stderr_bytes),
        (100_000, 100_000)
    );
    Ok(())
}
#[test]
fn actual_zero_does_not_satisfy_expected_one_and_signal_is_distinct() -> TestResult {
    for (script, expected) in [
        ("exit 0", ProcessStatus::Exit(0)),
        ("exit 1", ProcessStatus::Exit(1)),
        ("kill -TERM $$", ProcessStatus::Signal(15)),
    ] {
        let root = assert_fs::TempDir::new()?;
        let program = fixture(root.path(), "status", script)?;
        let result = ProcessRunner::new(policy())
            .execute(&command(root.path(), program), &captures(root.path()))?;
        require_eq!(result.status, expected);
        require_eq!(
            result.check_expected(1).is_ok(),
            expected == ProcessStatus::Exit(1)
        );
    }
    Ok(())
}
#[test]
fn quota_stops_output_and_retains_only_bounded_bytes() -> TestResult {
    for (script, stream) in [
        ("while :; do printf 'abcdefgh'; done", Stream::Stdout),
        ("while :; do printf 'abcdefgh' >&2; done", Stream::Stderr),
    ] {
        let root = assert_fs::TempDir::new()?;
        let program = fixture(root.path(), "quota", script)?;
        let mut limits = policy();
        limits.max_stream_bytes = 1000;
        let paths = captures(root.path());
        let result = ProcessRunner::new(limits).execute(&command(root.path(), program), &paths)?;
        require_eq!(result.stopped, Some(StopReason::OutputLimit(stream)));
        require!(fs::metadata(paths.stdout)?.len() <= 1000);
        require!(fs::metadata(paths.stderr)?.len() <= 1000);
    }
    Ok(())
}
#[test]
fn timeout_and_cancellation_remove_sleeping_descendants() -> TestResult {
    for cancel in [false, true] {
        let root = assert_fs::TempDir::new()?;
        let program = fixture(
            root.path(),
            "parent",
            "trap 'wait; exit 0' TERM; (trap 'wait; exit 0' TERM; sleep 30 & echo $! > child.pid; wait) & echo $! > middle.pid; wait",
        )?;
        let spec = command(root.path(), program);
        let mut limits = policy();
        // Allow fixture startup under workspace concurrency; descendant liveness
        // is still checked independently with bounded OS probes after return.
        limits.timeout = Duration::from_secs(1);
        let token = limits.cancellation.clone();
        let marker = root.join("child.pid");
        let cancelling = std::thread::spawn(move || {
            if cancel {
                let start = Instant::now();
                while !marker.exists() && start.elapsed() < Duration::from_secs(2) {
                    std::thread::sleep(Duration::from_millis(5));
                }
                token.store(true, Ordering::Relaxed);
            }
        });
        let result = ProcessRunner::new(limits).execute(&spec, &captures(root.path()))?;
        cancelling
            .join()
            .map_err(|_| "cancellation thread panicked")?;
        require_eq!(
            result.stopped,
            Some(if cancel {
                StopReason::Cancelled
            } else {
                StopReason::Timeout
            })
        );
        for name in ["middle.pid", "child.pid"] {
            assert_gone(&fs::read_to_string(root.join(name)).map_err(|error| {
                format!(
                    "missing {name}: {error}; stderr: {:?}",
                    fs::read_to_string(root.join("stderr"))
                )
            })?)?;
        }
    }
    Ok(())
}
fn assert_gone(value: &str) -> TestResult {
    let raw = value.trim().parse::<i32>()?;
    if raw <= 1 {
        return Err("invalid fixture pid".into());
    }
    let pid = rustix::process::Pid::from_raw(raw).ok_or("zero pid")?;
    let start = Instant::now();
    loop {
        match rustix::process::test_kill_process(pid) {
            Err(rustix::io::Errno::SRCH) => return Ok(()),
            Err(error) => return Err(error.into()),
            Ok(()) if start.elapsed() < Duration::from_secs(2) => {
                std::thread::sleep(Duration::from_millis(5));
            }
            Ok(()) => return Err(format!("fixture descendant {raw} survived cleanup").into()),
        }
    }
}

#[test]
fn sigkill_follows_one_second_grace_for_a_term_resistant_child() -> TestResult {
    let root = assert_fs::TempDir::new()?;
    let program = fixture(
        root.path(),
        "resistant",
        "trap '' TERM; echo $$ > resistant.pid; while :; do :; done",
    )?;
    let mut limits = policy();
    limits.timeout = Duration::from_millis(250);
    let start = Instant::now();
    let result = ProcessRunner::new(limits)
        .execute(&command(root.path(), program), &captures(root.path()))?;
    require_eq!(result.stopped, Some(StopReason::Timeout));
    require_eq!(result.status, ProcessStatus::Signal(9));
    require!(start.elapsed() >= Duration::from_secs(1));
    assert_gone(&fs::read_to_string(root.join("resistant.pid"))?)?;
    Ok(())
}

#[test]
fn explicit_context_and_all_output_boundaries_are_observable() -> TestResult {
    use cli_bench::{CommandInput, CommandOutput};
    for output in [
        CommandOutput::Capture,
        CommandOutput::DrainedPipe,
        CommandOutput::Discard,
        CommandOutput::File("placeholder".into()),
    ] {
        let root = assert_fs::TempDir::new()?;
        let program = fixture(
            root.path(),
            "context",
            "[ -f /dev/stdin ] || exit 11; [ \"$TEST_VALUE\" = 'kept literal' ] || exit 12; [ -z \"${HOME+x}\" ] || exit 13; printf 'payload'; printf 'diagnostic' >&2; printf '%s' \"$PWD\" > observed.cwd",
        )?;
        let mut spec = command(root.path(), program);
        fs::write(root.join("input"), "input")?;
        spec.stdin = CommandInput::File(root.join("input"));
        spec.environment
            .insert("TEST_VALUE".into(), "kept literal".into());
        spec.stdout = if matches!(output, CommandOutput::File(_)) {
            CommandOutput::File(root.join("sink"))
        } else {
            output
        };
        let paths = captures(root.path());
        let result = ProcessRunner::new(policy()).execute(&spec, &paths)?;
        result.check_expected(0)?;
        require_eq!(fs::read(&paths.stderr)?, b"diagnostic");
        require_eq!(
            fs::canonicalize(fs::read_to_string(root.join("observed.cwd"))?)?,
            fs::canonicalize(root.path())?
        );
        match spec.stdout {
            CommandOutput::Capture => require_eq!(fs::read(paths.stdout)?, b"payload"),
            CommandOutput::File(path) => require_eq!(fs::read(path)?, b"payload"),
            CommandOutput::DrainedPipe => {
                require_eq!(result.stdout_bytes, 7);
                require!(!paths.stdout.exists());
            }
            CommandOutput::Discard => {
                require_eq!(result.stdout_bytes, 0);
                require!(!paths.stdout.exists());
            }
        }
    }
    Ok(())
}

#[test]
fn deadline_remains_active_while_a_pipe_producer_is_blocked() -> TestResult {
    let root = assert_fs::TempDir::new()?;
    let producer = fixture(
        root.path(),
        "producer",
        "echo $$ > producer.pid; trap 'exit 0' TERM; while :; do printf 'abcdefghijklmnopqrstuvwxyz'; done",
    )?;
    let consumer = fixture(
        root.path(),
        "consumer",
        "echo $$ > consumer.pid; trap 'wait; exit 0' TERM; sleep 30 & echo $! > sleep.pid; wait",
    )?;
    let parent = fixture(
        root.path(),
        "pipeline",
        "trap 'wait; exit 0' TERM; \"$1\" | \"$2\" & wait",
    )?;
    let mut spec = command(root.path(), parent);
    spec.argv = vec![
        producer.to_str().ok_or("non-UTF-8 fixture path")?.into(),
        consumer.to_str().ok_or("non-UTF-8 fixture path")?.into(),
    ];
    let mut limits = policy();
    // Allow fixture startup under workspace concurrency; descendant liveness
    // is still checked independently with bounded OS probes after return.
    limits.timeout = Duration::from_secs(1);
    let result = ProcessRunner::new(limits).execute(&spec, &captures(root.path()))?;
    require_eq!(result.stopped, Some(StopReason::Timeout));
    for name in ["producer.pid", "consumer.pid", "sleep.pid"] {
        assert_gone(&fs::read_to_string(root.join(name))?)?;
    }
    Ok(())
}

#[test]
fn pipeline_uses_real_pipe_stdin_and_literal_positional_arguments() -> TestResult {
    use cli_bench::{
        CommandOutput, InvocationScope, MeasurementProfile, Role, StdinPolicy, resolve_invocation,
    };
    for pipe in [false, true] {
        let root = assert_fs::TempDir::new()?;
        let probe = fixture(
            root.path(),
            "probe ' ; $",
            "if [ -p /dev/stdin ]; then printf 'pipe\\000'; elif [ -f /dev/stdin ]; then printf 'file\\000'; else exit 21; fi; printf '%s\\000' \"$@\"; /bin/cat",
        )?;
        let (mut case, bindings, datasets) = common::invocation_inputs(root.path(), probe)?;
        case.argv = ["", "'quoted'", "$(touch injected)", "* ; &", "@@literal"]
            .map(str::to_owned)
            .into();
        case.io.stdin = if pipe {
            StdinPolicy::Pipe {
                dataset: "tiny".into(),
            }
        } else {
            StdinPolicy::RegularFile {
                dataset: "tiny".into(),
            }
        };
        let mut invocation = resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )?;
        require_eq!(
            invocation.scope,
            if pipe {
                InvocationScope::Pipeline
            } else {
                InvocationScope::Direct
            }
        );
        invocation.command.stdout = CommandOutput::Capture;
        let paths = captures(root.path());
        let result = ProcessRunner::new(policy()).execute(&invocation.command, &paths)?;
        result.check_expected(invocation.expected_status)?;
        let expected = if pipe {
            b"pipe\0\0'quoted'\0$(touch injected)\0* ; &\0@literal\0small explicit fixture\0\xff"
                .as_slice()
        } else {
            b"file\0\0'quoted'\0$(touch injected)\0* ; &\0@literal\0small explicit fixture\0\xff"
                .as_slice()
        };
        require_eq!(fs::read(paths.stdout)?, expected);
        require_eq!(fs::read(paths.stderr)?, b"");
        require!(!root.join("injected").exists());
    }
    Ok(())
}

#[test]
fn producer_failure_invalidates_an_otherwise_successful_pipeline() -> TestResult {
    use cli_bench::{
        BoundTool, MeasurementProfile, Role, StdinPolicy, ToolIdentity, fingerprint,
        resolve_invocation,
    };
    let root = assert_fs::TempDir::new()?;
    let consumer = fixture(root.path(), "consumer", "/bin/cat; exit 0")?;
    let producer = fixture(root.path(), "failing producer", "printf partial; exit 17")?;
    let (mut case, mut bindings, datasets) = common::invocation_inputs(root.path(), consumer)?;
    case.io.stdin = StdinPolicy::Pipe {
        dataset: "tiny".into(),
    };
    bindings.pipeline.as_mut().ok_or("missing pipeline")?.cat = BoundTool {
        path: producer.clone(),
        identity: ToolIdentity {
            file: fingerprint(&producer)?,
            version: "original failure fixture".into(),
        },
    };
    let invocation = resolve_invocation(
        &case,
        Role::Candidate,
        MeasurementProfile::Full,
        &bindings,
        &datasets,
        root.path(),
    )?;
    let result =
        ProcessRunner::new(policy()).execute(&invocation.command, &captures(root.path()))?;
    require_eq!(result.status, ProcessStatus::Exit(17));
    require!(result.check_expected(invocation.expected_status).is_err());
    Ok(())
}

#[test]
fn file_limit_stops_live_writers_and_rejects_fast_exit_overflow() -> TestResult {
    for delay in ["/bin/sleep 2", "exit 0"] {
        let root = assert_fs::TempDir::new()?;
        let output = root.join("generated");
        let program = fixture(
            root.path(),
            "file-writer",
            &format!("printf '0123456789abcdef' > \"$1\"; {delay}"),
        )?;
        let mut spec = command(root.path(), program);
        spec.argv = vec![output.to_str().ok_or("non-UTF-8 fixture path")?.into()];
        let started = Instant::now();
        let outcome = ProcessRunner::new(policy()).execute_with_file_limit(
            &spec,
            &captures(root.path()),
            &cli_bench::OutputFileLimit {
                path: output,
                max_bytes: 8,
            },
        )?;
        require_eq!(outcome.stopped, Some(StopReason::FileLimit));
        require!(outcome.check_expected(0).is_err());
        require!(started.elapsed() < Duration::from_secs(2));
    }
    Ok(())
}

#[path = "common/production_validation.rs"]
mod validation_support;
#[test]
fn mkdir_receives_same_initial_state_each_time() -> validation_support::TestResult {
    use cli_bench::*;
    for existing in [false, true] {
        let body = if existing {
            "[ -d \"$1\" ] || exit 9\n/bin/mkdir -p \"$1\""
        } else {
            "[ ! -e \"$1\" ] || exit 9\n/bin/mkdir \"$1\""
        };
        let mut fixture = validation_support::Fixture::new(body, body)?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv = vec!["@scratch:target".into()];
        case.correctness = vec![
            CorrectnessRule::DirectoryTree {
                paths: vec!["target".into()],
                compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
            },
            CorrectnessRule::EmptyStderr {},
        ];
        case.mutation = MutationSetup::Directories {
            paths: if existing {
                vec!["target".into()]
            } else {
                vec![]
            },
        };
        let id = CaseId::new(case.id.clone())?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(cli_bench::MeasurementProfile::Full)?,
            &mut writer,
            &fixture.runner,
        )?;
        let prepared = validated.prepared();
        let spec = prepared.cases().first().ok_or("case")?;
        let scratch = prepared.scratch(&id).ok_or("scratch")?;
        for iteration in 0..3 {
            reset_case(spec, validated.datasets(), scratch)?;
            let invocation = resolve_invocation(
                spec,
                Role::Candidate,
                prepared.profile(),
                validated.roles(),
                validated.datasets(),
                scratch.path(),
            )?;
            let captures = CapturePaths {
                stdout: fixture
                    .root
                    .path()
                    .join(format!("sample-{iteration}.stdout")),
                stderr: fixture
                    .root
                    .path()
                    .join(format!("sample-{iteration}.stderr")),
            };
            fixture
                .runner
                .execute(&invocation.command, &captures)?
                .check_expected(0)?;
            validated.verify_effects(&id, Role::Candidate)?;
        }
        std::fs::write(scratch.path().join("unexpected-file"), b"extra")?;
        if validated.verify_effects(&id, Role::Candidate).is_ok() {
            return Err("post-invocation file effect accepted".into());
        }
    }
    Ok(())
}
