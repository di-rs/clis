use cli_bench::*;
#[path = "common/validation.rs"]
mod validation_support;
use validation_support::{Fixture, TestResult};

fn engine(fixture: &Fixture, mode: &str) -> Result<BoundTool, BenchError> {
    let (before, after) = if mode == "force-zero" {
        ("", "status=0")
    } else {
        (mode, "")
    };
    let version = if mode == "observed-version" {
        "hyperfine 1.19.0"
    } else {
        "hyperfine 1.20.0"
    };
    let body = format!(
        r#"
if [ "$1" = '--version' ]; then printf '{version}\n'; exit 0; fi
output=''
while [ "$#" -gt 0 ]; do
    if [ "$1" = '--export-json' ]; then shift; output=$1; fi
    command=$1
    shift
done
{before}
/bin/sh -c "$command"
status=$?
{after}
printf '{{"results":[{{"command":"%s","times":[0.001],"exit_codes":[%s]}}]}}\n' "$command" "$status" > "$output"
printf 'fixture engine warning\n' >&2
"#
    );
    let path = validation_support::script(fixture.root.path(), "engine", &body)?;
    Ok(BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&path)?,
            version: "hyperfine 1.20.0".into(),
        },
        path,
    })
}
fn prepare_smoke<'a>(
    fixture: &'a Fixture,
    writer: &mut RunWriter,
) -> Result<ValidatedExperiment<'a>, BenchError> {
    let prepared = prepare_experiment(
        &fixture.measurement_lock,
        &ExperimentPreparation {
            run: &fixture.request,
            suite: &fixture.suite,
            profile: MeasurementProfile::Smoke,
            selected_cases: &[],
            expected_datasets: None,
        },
        &fixture.store,
        &fixture.runner,
    )?;
    validate_experiment(prepared, writer, &fixture.runner)
}
#[test]
fn public_timing_retains_schedule_warnings_samples_and_final_gate() -> TestResult {
    let fixture = Fixture::new("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
    let tool = engine(&fixture, "")?;
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let validated = prepare_smoke(&fixture, &mut writer)?;
    let samples = measure_timing(&validated, &mut writer, &fixture.runner, &tool)?;
    require(
        (samples.len()) == (8),
        &format!("assertion failed: {}", stringify!((samples.len()) == (8))),
    )?;
    require(
        (samples.iter().map(|s| s.role).collect::<Vec<_>>())
            == ([
                Role::Previous,
                Role::Previous,
                Role::Candidate,
                Role::Candidate,
                Role::Candidate,
                Role::Candidate,
                Role::Previous,
                Role::Previous,
            ]),
        &format!(
            "assertion failed: {}",
            stringify!(
                (samples.iter().map(|s| s.role).collect::<Vec<_>>())
                    == ([
                        Role::Previous,
                        Role::Previous,
                        Role::Candidate,
                        Role::Candidate,
                        Role::Candidate,
                        Role::Candidate,
                        Role::Previous,
                        Role::Previous
                    ])
            )
        ),
    )?;
    for sample in samples {
        require(
            (sample.status) == (0),
            &format!("assertion failed: {}", stringify!((sample.status) == (0))),
        )?;
        require(
            writer.path().join(sample.raw_json).is_file(),
            &format!(
                "assertion failed: {}",
                stringify!(writer.path().join(sample.raw_json).is_file())
            ),
        )?;
        require(
            writer.path().join(sample.raw_stdout).is_file(),
            &format!(
                "assertion failed: {}",
                stringify!(writer.path().join(sample.raw_stdout).is_file())
            ),
        )?;
        require(
            (std::fs::read(writer.path().join(sample.raw_stderr))?)
                == (b"fixture engine warning\n"),
            &format!(
                "assertion failed: {}",
                stringify!(
                    (std::fs::read(writer.path().join(sample.raw_stderr))?)
                        == (b"fixture engine warning\n")
                )
            ),
        )?;
    }
    require(
        writer.path().join("raw/timing/schedule.json").is_file(),
        &format!(
            "assertion failed: {}",
            stringify!(writer.path().join("raw/timing/schedule.json").is_file())
        ),
    )?;
    require(
        writer
            .path()
            .join("raw/timing/final-last-line/report.json")
            .is_file(),
        &format!(
            "assertion failed: {}",
            stringify!(
                writer
                    .path()
                    .join("raw/timing/final-last-line/report.json")
                    .is_file()
            )
        ),
    )?;
    require(
        writer.path().join("measurement-lock.json").is_file(),
        &format!(
            "assertion failed: {}",
            stringify!(writer.path().join("measurement-lock.json").is_file())
        ),
    )?;
    Ok(())
}
#[test]
fn engine_version_identity_timeout_and_command_mismatch_fail() -> TestResult {
    let fixture = Fixture::new("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
    for mode in [
        "wrong-version",
        "observed-version",
        "wrong-command",
        "timeout",
        "changed-engine",
    ] {
        let mut tool = engine(
            &fixture,
            match mode {
                "observed-version" => "observed-version",
                "wrong-command" => "command=/wrong/command",
                "timeout" => "/bin/sleep 10",
                _ => "",
            },
        )?;
        if mode == "wrong-version" {
            tool.identity.version = "hyperfine 1.19.0".into();
        }
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(fixture.prepare()?, &mut writer, &fixture.runner)?;
        if mode == "changed-engine" {
            std::fs::write(&tool.path, b"changed")?;
        }
        let runner = ProcessRunner::new(ExecutionPolicy {
            timeout: std::time::Duration::from_millis(150),
            max_stream_bytes: 4096,
            cancellation: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        });
        require(
            measure_timing(&validated, &mut writer, &runner, &tool).is_err(),
            &format!("accepted {mode}"),
        )?;
    }
    Ok(())
}
#[test]
fn final_gate_rechecks_output_after_measurement() -> TestResult {
    let fixture = Fixture::new(
        r#"n=0
if [ -f "$HOME/count" ]; then read -r n < "$HOME/count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/count"
if [ "$n" -gt 8 ]; then printf 'WRONG\n'; else printf 'EFGH\n'; fi"#,
        "printf 'EFGH\\n'",
    )?;
    let tool = engine(&fixture, "")?;
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let validated = prepare_smoke(&fixture, &mut writer)?;
    require(
        measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err(),
        &format!(
            "assertion failed: {}",
            stringify!(measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err())
        ),
    )?;
    let report: ValidationReport = serde_json::from_slice(&std::fs::read(
        writer.path().join("raw/timing/final-last-line/report.json"),
    )?)?;
    require(
        !report.passed(),
        &format!("assertion failed: {}", stringify!(!report.passed())),
    )?;
    Ok(())
}

#[test]
#[ignore = "opt-in Hyperfine 1.20.0 adapter contracts; set CLI_BENCH_HYPERFINE"]
fn native_hyperfine_adapter_contracts_plumbing_only() -> TestResult {
    let path = std::fs::canonicalize(
        std::env::var_os("CLI_BENCH_HYPERFINE").ok_or("set CLI_BENCH_HYPERFINE explicitly")?,
    )?;
    let tool = BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&path)?,
            version: "hyperfine 1.20.0".into(),
        },
        path,
    };
    for mode in [
        "zero",
        "one",
        "pipe",
        "file-io",
        "quoted-argv",
        "zero-for-one",
    ] {
        let mut fixture = native_fixture(mode)?;
        // Retain real engine warnings/JSON and final-gate captures for inspection.
        fixture.root = fixture.root.into_persistent();
        eprintln!(
            "plumbing-only native {mode} evidence: {}",
            fixture.root.display()
        );
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = prepare_smoke(&fixture, &mut writer)?;
        let result = measure_timing(&validated, &mut writer, &fixture.runner, &tool);
        if mode == "zero-for-one" {
            require(result.is_err(), "actual zero accepted under expected one")?;
        } else {
            require(
                (result?.len()) == (8),
                &format!("assertion failed: {}", stringify!((result?.len()) == (8))),
            )?;
        }
    }
    Ok(())
}

#[test]
fn exact_expected_one_and_mutation_reset_are_checked_after_each_invocation() -> TestResult {
    {
        let mut fixture = Fixture::new("printf 'EFGH\\n'; exit 1", "printf 'EFGH\\n'; exit 1")?;
        fixture
            .suite
            .cases
            .first_mut()
            .ok_or("case")?
            .expected_status = 1;
        let tool = engine(&fixture, "force-zero")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = prepare_smoke(&fixture, &mut writer)?;
        require(
            measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(
                    measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err()
                )
            ),
        )?;
        require(
            !writer.path().join("raw/timing/samples.json").exists(),
            &format!(
                "assertion failed: {}",
                stringify!(!writer.path().join("raw/timing/samples.json").exists())
            ),
        )?;
    }
    let mut fixture = Fixture::new("/bin/mkdir -m 700 \"$1\"", "/bin/mkdir -m 700 \"$1\"")?;
    let case = fixture.suite.cases.first_mut().ok_or("case")?;
    case.argv = vec!["@scratch:dir".into()];
    case.correctness = vec![CorrectnessRule::DirectoryTree {
        paths: vec!["dir".into()],
        compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
    }];
    let tool = engine(&fixture, "")?;
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let validated = prepare_smoke(&fixture, &mut writer)?;
    require(
        (measure_timing(&validated, &mut writer, &fixture.runner, &tool)?.len()) == (8),
        &format!(
            "assertion failed: {}",
            stringify!(
                (measure_timing(&validated, &mut writer, &fixture.runner, &tool)?.len()) == (8)
            )
        ),
    )?;
    require(
        measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err(),
        "reused raw evidence directory",
    )?;
    Ok(())
}

fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

fn native_fixture(mode: &str) -> Result<Fixture, Box<dyn std::error::Error>> {
    use std::fmt::Write;
    let body = match mode {
        "one" => "printf 'EFGH\\n'; exit 1",
        "pipe" => "test -p /dev/stdin || exit 9; test -p /dev/stdout || exit 8; /bin/cat",
        "file-io" => "test -f /dev/stdin || exit 9; /bin/cat",
        "quoted-argv" => {
            r#"[ "$#" -eq 7 ] && [ "$1" = '' ] && [ "$2" = 'a b' ] && [ "$3" = "'single'" ] && [ "$4" = '"double"' ] && [ "$5" = '$dollar' ] && [ "$6" = '`backtick`' ] && [ "$7" = 'line
next' ] || exit 9
printf '%s\0' "$@""#
        }
        "zero-for-one" => {
            r#"n=0
if [ -f "$HOME/count" ]; then read -r n < "$HOME/count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/count"
printf 'EFGH\n'; if [ "$n" -le 3 ]; then exit 1; fi"#
        }
        _ => "printf 'EFGH\\n'",
    };
    let previous = if mode == "zero-for-one" {
        "printf 'EFGH\\n'; exit 1"
    } else {
        body
    };
    let mut fixture = Fixture::new(body, previous)?;
    let case = fixture.suite.cases.first_mut().ok_or("case")?;
    if matches!(mode, "one" | "zero-for-one") {
        case.expected_status = 1;
    }
    if mode == "pipe" {
        case.argv.clear();
        case.io.stdin = StdinPolicy::Pipe {
            dataset: "tiny".into(),
        };
        case.correctness = vec![
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "414243440a454647480a".into(),
            },
            CorrectnessRule::EmptyStderr {},
        ];
        let bound = |name: &str| -> Result<BoundTool, BenchError> {
            Ok(BoundTool {
                path: name.into(),
                identity: ToolIdentity {
                    file: fingerprint(std::path::Path::new(name))?,
                    version: "native system adapter fixture".into(),
                },
            })
        };
        fixture.request.pipeline = Some(PipelineTools {
            bash: bound("/bin/bash")?,
            cat: bound("/bin/cat")?,
        });
    }
    if mode == "file-io" {
        case.argv.clear();
        case.io.stdin = StdinPolicy::RegularFile {
            dataset: "tiny".into(),
        };
        case.io.stdout = StdoutPolicy::ScratchFile {
            path: "output".into(),
        };
        case.correctness = vec![
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "414243440a454647480a".into(),
            },
            CorrectnessRule::EmptyStderr {},
        ];
    }
    if mode == "quoted-argv" {
        case.argv = [
            "",
            "a b",
            "'single'",
            "\"double\"",
            "$dollar",
            "`backtick`",
            "line\nnext",
        ]
        .map(str::to_owned)
        .to_vec();
        let bytes: Vec<_> = case
            .argv
            .iter()
            .flat_map(|arg| arg.bytes().chain([0]))
            .collect();
        let mut hex = String::new();
        for byte in bytes {
            write!(hex, "{byte:02x}")?;
        }
        case.correctness = vec![
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex,
            },
            CorrectnessRule::EmptyStderr {},
        ];
    }
    Ok(fixture)
}

#[test]
fn missing_mutation_effect_stops_before_the_next_sample() -> TestResult {
    let mut fixture = Fixture::new(
        r#"n=0
if [ -f "$HOME/candidate-count" ]; then read -r n < "$HOME/candidate-count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/candidate-count"
if [ "$n" -lt 4 ]; then /bin/mkdir -m 700 "$1"; fi"#,
        "/bin/mkdir -m 700 \"$1\"",
    )?;
    let case = fixture.suite.cases.first_mut().ok_or("case")?;
    case.argv = vec!["@scratch:dir".into()];
    case.correctness = vec![CorrectnessRule::DirectoryTree {
        paths: vec!["dir".into()],
        compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
    }];
    let tool = engine(&fixture, "")?;
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let validated = prepare_smoke(&fixture, &mut writer)?;
    require(
        measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err(),
        "missing directory effect accepted",
    )?;
    require(
        std::fs::read_to_string(fixture.request.home.join("candidate-count"))?.trim() == "4",
        "driver continued after bad mutation",
    )?;
    Ok(())
}
