use crate::BenchError;

/// One engine-exported observation; command identity is checked by the adapter.
#[derive(Debug, PartialEq)]
pub struct TimingObservation {
    pub command: String,
    pub seconds: f64,
    pub status: i32,
}
/// Parse exactly one finite positive sample with the exact declared status.
/// # Errors
/// Rejects malformed exports, missing/multiple observations and status mismatches.
pub fn parse_hyperfine_sample(json: &str, expected: i32) -> Result<TimingObservation, BenchError> {
    #[derive(serde::Deserialize)]
    struct Export {
        results: Vec<ResultEntry>,
    }
    #[derive(serde::Deserialize)]
    struct ResultEntry {
        command: String,
        times: Vec<f64>,
        exit_codes: Vec<i32>,
    }
    let mut export: Export = serde_json::from_str(json)?;
    if export.results.len() != 1 || !(0..=255).contains(&expected) {
        return Err(BenchError::Execution(
            "expected one engine result and a valid expected status".into(),
        ));
    }
    let entry = export.results.remove(0);
    if entry.times.len() != 1 || entry.exit_codes != [expected] {
        return Err(BenchError::Execution(
            "expected exactly one time and the declared exit status".into(),
        ));
    }
    let seconds = *entry
        .times
        .first()
        .ok_or_else(|| BenchError::Execution("missing sample time".into()))?;
    if !seconds.is_finite() || seconds <= 0.0 {
        return Err(BenchError::Execution(
            "sample time must be finite and positive".into(),
        ));
    }
    Ok(TimingObservation {
        command: entry.command,
        seconds,
        status: expected,
    })
}

/// Timing batches preserve forward then reverse role ordering.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum TimingBatch {
    Forward,
    Reverse,
}
/// Checked warmups are untimed; samples each use a fresh engine process.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum TimingKind {
    Warmup,
    Sample,
}
/// One scheduled invocation. Ordinals are one-based within role/batch/kind.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TimingStep {
    pub case: String,
    pub role: crate::Role,
    pub batch: TimingBatch,
    pub ordinal: u32,
    pub kind: TimingKind,
}
/// Build the exact approved order, accepting only a successful correctness gate.
///
/// ```compile_fail
/// use cli_bench::{PreparedExperiment, timing_schedule};
/// fn cannot_measure_unvalidated(prepared: &PreparedExperiment<'_>) {
///     timing_schedule(prepared);
/// }
/// ```
#[must_use]
pub fn timing_schedule(validated: &crate::ValidatedExperiment<'_>) -> Vec<TimingStep> {
    schedule(
        validated
            .prepared()
            .cases()
            .iter()
            .map(|case| case.id.as_str()),
        validated.prepared().profile(),
        &validated.roles().roles.keys().copied().collect::<Vec<_>>(),
    )
}
fn schedule<'a>(
    cases: impl Iterator<Item = &'a str>,
    profile: crate::MeasurementProfile,
    roles: &[crate::Role],
) -> Vec<TimingStep> {
    let policy = profile.policy();
    let mut steps = vec![];
    for case in cases {
        for batch in [TimingBatch::Forward, TimingBatch::Reverse] {
            let ordered: Vec<_> = match batch {
                TimingBatch::Forward => roles.to_vec(),
                TimingBatch::Reverse => roles.iter().rev().copied().collect(),
            };
            for role in ordered {
                for (kind, count) in [
                    (TimingKind::Warmup, policy.warmups),
                    (TimingKind::Sample, policy.samples_per_batch),
                ] {
                    for ordinal in 1..=count {
                        steps.push(TimingStep {
                            case: case.into(),
                            role,
                            batch,
                            ordinal,
                            kind,
                        });
                    }
                }
            }
        }
    }
    steps
}

/// One accepted elapsed-time sample; raw paths are relative to its run directory.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TimingSample {
    pub case: String,
    pub role: crate::Role,
    pub batch: TimingBatch,
    pub ordinal: u32,
    pub seconds: f64,
    pub status: i32,
    pub raw_json: std::path::PathBuf,
    pub raw_stdout: std::path::PathBuf,
    pub raw_stderr: std::path::PathBuf,
}
/// Execute the controlled schedule with a caller-identified Hyperfine 1.20.0.
/// The validated experiment retains the measurement lock throughout this call.
/// # Errors
/// Rejects changed identities, engine/child failures and final correctness failures.
pub fn measure_timing(
    validated: &crate::ValidatedExperiment<'_>,
    writer: &mut crate::RunWriter,
    runner: &crate::ProcessRunner,
    engine: &crate::BoundTool,
) -> Result<Vec<TimingSample>, BenchError> {
    let prepared = validated.prepared();
    if !writer.matches_suite(prepared.suite()) {
        return Err(BenchError::Execution(
            "timing writer suite differs from validated suite".into(),
        ));
    }
    validated.revalidate()?;
    let limits = &prepared.suite().limits;
    let runner = runner.bounded(
        std::time::Duration::from_secs(limits.sample_timeout_seconds),
        limits.max_stream_bytes,
    );
    let raw = writer.path().join("raw/timing");
    if std::fs::read_dir(&raw)?.next().is_some() {
        return Err(BenchError::Execution(
            "timing evidence already exists; begin a fresh run".into(),
        ));
    }
    let schedule = timing_schedule(validated);
    crate::store::atomic_json(&raw.join("schedule.json"), &schedule)?;
    identify_engine(engine, prepared, &raw, &runner)?;
    let mut samples = vec![];
    for case in prepared.cases() {
        let id = crate::CaseId::new(case.id.clone())?;
        for (index, step) in schedule
            .iter()
            .enumerate()
            .filter(|(_, step)| step.case == case.id)
        {
            if let Some(sample) = execute_step(validated, writer, &runner, engine, step, index)? {
                samples.push(sample);
            }
        }
        validated.final_case_check(&id, &raw.join(format!("final-{}", case.id)), &runner)?;
        writer.append_event(&crate::RunEvent::Stage(format!(
            "timing and final correctness passed for {}",
            case.id
        )))?;
    }
    validated.revalidate()?;
    crate::verify_file(&engine.path, &engine.identity.file)?;
    crate::store::atomic_json(&raw.join("samples.json"), &samples)?;
    Ok(samples)
}
fn execute_step(
    validated: &crate::ValidatedExperiment<'_>,
    writer: &crate::RunWriter,
    runner: &crate::ProcessRunner,
    engine: &crate::BoundTool,
    step: &TimingStep,
    index: usize,
) -> Result<Option<TimingSample>, BenchError> {
    let prepared = validated.prepared();
    let case = prepared
        .cases()
        .iter()
        .find(|case| case.id == step.case)
        .ok_or_else(|| BenchError::Execution("schedule contains unknown case".into()))?;
    let id = crate::CaseId::new(case.id.clone())?;
    let scratch = prepared
        .scratch(&id)
        .ok_or_else(|| BenchError::Execution("missing validated scratch".into()))?;
    let raw = writer.path().join("raw/timing");
    validated.revalidate()?;
    crate::verify_file(&engine.path, &engine.identity.file)?;
    crate::reset_case(case, validated.datasets(), scratch)?;
    let invocation = crate::resolve_invocation(
        case,
        step.role,
        prepared.profile(),
        validated.roles(),
        validated.datasets(),
        scratch.path(),
    )?;
    let raw_name = format!(
        "{index:06}-{}-{:?}-{:?}-{:?}-{}",
        step.case, step.batch, step.role, step.kind, step.ordinal
    );
    let paths = crate::CapturePaths {
        stdout: raw.join(format!("{raw_name}.stdout")),
        stderr: raw.join(format!("{raw_name}.stderr")),
    };
    crate::store::atomic_json(&raw.join(format!("{raw_name}.step.json")), step)?;
    match step.kind {
        TimingKind::Warmup => {
            let outcome = runner.execute(&invocation.command, &paths)?;
            crate::store::atomic_json(
                &raw.join(format!("{raw_name}.outcome.json")),
                &serde_json::json!({"status": format!("{:?}", outcome.status), "stopped": format!("{:?}", outcome.stopped)}),
            )?;
            let status = outcome.check_expected(invocation.expected_status);
            let effects = validated.verify_effects(&id, step.role);
            status?;
            effects?;
        }
        TimingKind::Sample => {
            let json = raw.join(format!("{raw_name}.json"));
            let (command, expected_command) = engine_command(engine, &invocation, &json)?;
            crate::store::atomic_json(
                &raw.join(format!("{raw_name}.command.json")),
                &serde_json::json!({"engine": engine.identity, "argv": command.argv, "workload": expected_command, "scope": format!("{:?}", invocation.scope)}),
            )?;
            let outcome = runner.execute_with_file_limit(
                &command,
                &paths,
                &crate::OutputFileLimit {
                    path: json.clone(),
                    max_bytes: 1_048_576,
                },
            )?;
            crate::store::atomic_json(
                &raw.join(format!("{raw_name}.outcome.json")),
                &serde_json::json!({"status": format!("{:?}", outcome.status), "stopped": format!("{:?}", outcome.stopped)}),
            )?;
            let status = outcome.check_expected(0);
            let effects = validated.verify_effects(&id, step.role);
            status?;
            effects?;
            let observation = parse_hyperfine_sample(
                &std::fs::read_to_string(&json)?,
                invocation.expected_status,
            )?;
            if observation.command != expected_command {
                return Err(BenchError::Execution(
                    "engine exported an unexpected command identity".into(),
                ));
            }
            let sample = TimingSample {
                case: step.case.clone(),
                role: step.role,
                batch: step.batch,
                ordinal: step.ordinal,
                seconds: observation.seconds,
                status: observation.status,
                raw_json: relative(writer, &json)?,
                raw_stdout: relative(writer, &paths.stdout)?,
                raw_stderr: relative(writer, &paths.stderr)?,
            };
            crate::store::atomic_json(&raw.join(format!("{raw_name}.sample.json")), &sample)?;
            return Ok(Some(sample));
        }
    }
    Ok(None)
}

fn relative(
    writer: &crate::RunWriter,
    path: &std::path::Path,
) -> Result<std::path::PathBuf, BenchError> {
    path.strip_prefix(writer.path())
        .map(std::path::Path::to_path_buf)
        .map_err(|_| BenchError::Execution("raw evidence escaped run".into()))
}
fn identify_engine(
    engine: &crate::BoundTool,
    prepared: &crate::PreparedExperiment<'_>,
    raw: &std::path::Path,
    runner: &crate::ProcessRunner,
) -> Result<(), BenchError> {
    if engine.identity.version.trim() != "hyperfine 1.20.0" {
        return Err(BenchError::Execution(
            "only Hyperfine 1.20.0 is supported".into(),
        ));
    }
    crate::verify_file(&engine.path, &engine.identity.file)?;
    let paths = crate::CapturePaths {
        stdout: raw.join("engine-version.stdout"),
        stderr: raw.join("engine-version.stderr"),
    };
    let command = crate::CommandSpec {
        program: engine.path.clone(),
        argv: vec!["--version".into()],
        cwd: prepared.roles().home.clone(),
        environment: crate::invocation::child_environment(prepared.roles())?,
        stdin: crate::CommandInput::Null,
        stdout: crate::CommandOutput::Capture,
    };
    runner.execute(&command, &paths)?.check_expected(0)?;
    if std::fs::read_to_string(&paths.stdout)?.trim() != "hyperfine 1.20.0" {
        return Err(BenchError::Execution(
            "observed engine version is not Hyperfine 1.20.0".into(),
        ));
    }
    crate::verify_file(&engine.path, &engine.identity.file)
}
fn engine_command(
    engine: &crate::BoundTool,
    invocation: &crate::Invocation,
    json: &std::path::Path,
) -> Result<(crate::CommandSpec, String), BenchError> {
    let workload = crate::invocation::hyperfine_command(&invocation.command)?;
    let input = match &invocation.command.stdin {
        crate::CommandInput::Null => "null".into(),
        crate::CommandInput::File(path) => crate::process::utf8_path(path)?.to_owned(),
    };
    let output = match &invocation.command.stdout {
        crate::CommandOutput::Discard => "null".into(),
        crate::CommandOutput::DrainedPipe => "pipe".into(),
        crate::CommandOutput::File(path) => crate::process::utf8_path(path)?.to_owned(),
        crate::CommandOutput::Capture => {
            return Err(BenchError::invalid(
                "timing requires a declared workload sink",
            ));
        }
    };
    let mut argv = vec![
        "--runs".into(),
        "1".into(),
        "--warmup".into(),
        "0".into(),
        "--shell=none".into(),
        "--input".into(),
        input,
        "--output".into(),
        output,
        "--export-json".into(),
        crate::process::utf8_path(json)?.into(),
    ];
    if invocation.expected_status != 0 {
        if invocation.scope != crate::InvocationScope::Direct {
            return Err(BenchError::invalid(
                "nonzero timing status is supported only for direct invocations",
            ));
        }
        argv.push(format!("--ignore-failure={}", invocation.expected_status));
    }
    argv.extend(["--".into(), workload.clone()]);
    Ok((
        crate::CommandSpec {
            program: engine.path.clone(),
            argv,
            cwd: invocation.command.cwd.clone(),
            environment: invocation.command.environment.clone(),
            stdin: crate::CommandInput::Null,
            stdout: crate::CommandOutput::Capture,
        },
        workload,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{
        require,
        timing_support::{engine, prepare_smoke},
        validation_support::TestResult,
    };
    use crate::*;

    #[test]
    fn parses_exact_nonzero_status_and_rejects_zero_for_expected_one() {
        assert!(
            parse_hyperfine_sample(include_str!("../tests/inputs/hyperfine-status-1.json"), 1)
                .is_ok()
        );
        assert!(
            parse_hyperfine_sample(include_str!("../tests/inputs/hyperfine-status-0.json"), 1)
                .is_err()
        );
    }

    #[test]
    fn rejects_invalid_cardinality_status_and_times() {
        for export in [
            r"{}",
            r#"{"results":[]}"#,
            r#"{"results":[{},{}]}"#,
            r#"{"results":[{"command":"x","times":[],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[1,2],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[1],"exit_codes":[]}]}"#,
            r#"{"results":[{"command":"x","times":[1],"exit_codes":[0,0]}]}"#,
            r#"{"results":[{"command":"x","times":[1],"exit_codes":[null]}]}"#,
            r#"{"results":[{"command":"x","times":[1],"exit_codes":[-9]}]}"#,
            r#"{"results":[{"command":"x","times":[1],"exit_codes":[256]}]}"#,
            r#"{"results":[{"command":"x","times":[null],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[NaN],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[1e999],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[-1],"exit_codes":[0]}]}"#,
            r#"{"results":[{"command":"x","times":[0],"exit_codes":[0]}]}"#,
            r#"{"results":[{"times":[1],"exit_codes":[0]}]}"#,
        ] {
            assert!(
                parse_hyperfine_sample(export, 0).is_err(),
                "accepted {export}"
            );
        }
        assert!(
            parse_hyperfine_sample(include_str!("../tests/inputs/hyperfine-status-0.json"), 0)
                .is_ok()
        );
    }
    #[test]
    fn schedules_full_and_smoke_in_case_batch_role_order_without_absent_roles() {
        use crate::{MeasurementProfile, Role};
        for profile in [MeasurementProfile::Full, MeasurementProfile::Smoke] {
            for roles in [
                vec![Role::Reference, Role::Previous, Role::Candidate],
                vec![Role::Previous, Role::Candidate],
            ] {
                let steps = schedule(["first", "second"].into_iter(), profile, &roles);
                let (warmups, samples) = if profile == MeasurementProfile::Full {
                    (3, 20)
                } else {
                    (1, 2)
                };
                assert_eq!(steps.len(), 2 * 2 * roles.len() * (warmups + samples));
                for case in ["first", "second"] {
                    for role in &roles {
                        assert_eq!(
                            steps
                                .iter()
                                .filter(|step| step.case == case
                                    && step.role == *role
                                    && step.kind == TimingKind::Sample)
                                .count(),
                            if profile == MeasurementProfile::Full {
                                40
                            } else {
                                4
                            }
                        );
                    }
                }
                let starts: Vec<_> = steps
                    .iter()
                    .filter(|step| step.kind == TimingKind::Warmup && step.ordinal == 1)
                    .map(|step| (step.case.as_str(), step.batch, step.role))
                    .collect();
                let mut expected = vec![];
                for case in ["first", "second"] {
                    for role in &roles {
                        expected.push((case, TimingBatch::Forward, *role));
                    }
                    for role in roles.iter().rev() {
                        expected.push((case, TimingBatch::Reverse, *role));
                    }
                }
                assert_eq!(starts, expected);
                for group in steps.chunks(warmups + samples) {
                    assert!(
                        group
                            .iter()
                            .take(warmups)
                            .all(|step| step.kind == TimingKind::Warmup)
                    );
                    assert!(
                        group
                            .iter()
                            .skip(warmups)
                            .all(|step| step.kind == TimingKind::Sample)
                    );
                }
            }
        }
    }

    #[test]
    fn engine_version_identity_timeout_and_command_mismatch_fail() -> TestResult {
        let fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
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
                    "wrong-command" => "wrong-command",
                    "timeout" => "/bin/sleep 10",
                    _ => "",
                },
            )?;
            if mode == "wrong-version" {
                tool.identity.version = "hyperfine 1.19.0".into();
            }
            let mut writer = fixture.store.begin_run(&fixture.suite)?;
            let validated = validate_experiment(
                fixture.prepare(crate::MeasurementProfile::Full)?,
                &mut writer,
                &fixture.runner,
            )?;
            if mode == "changed-engine" {
                std::fs::write(&tool.path, b"changed")?;
            }
            let runner = ProcessRunner::new(ExecutionPolicy {
                timeout: std::time::Duration::from_millis(150),
                max_stream_bytes: 4096,
                cancellation: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            });
            let error = measure_timing(&validated, &mut writer, &runner, &tool)
                .err()
                .ok_or_else(|| format!("accepted {mode}"))?;
            if mode == "wrong-command" {
                require(
                    error
                        .to_string()
                        .contains("engine exported an unexpected command identity"),
                    &format!("wrong command-identity rejection: {error}"),
                )?;
            }
        }
        Ok(())
    }

    #[test]
    fn final_gate_rechecks_output_after_measurement() -> TestResult {
        let fixture = crate::test_support::validation_fixture(
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
                stringify!(
                    measure_timing(&validated, &mut writer, &fixture.runner, &tool).is_err()
                )
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
    fn exact_expected_one_and_mutation_reset_are_checked_after_each_invocation() -> TestResult {
        {
            let mut fixture = crate::test_support::validation_fixture(
                "printf 'EFGH\\n'; exit 1",
                "printf 'EFGH\\n'; exit 1",
            )?;
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
        let mut fixture = crate::test_support::validation_fixture(
            "/bin/mkdir -m 700 \"$1\"",
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

    #[test]
    fn missing_mutation_effect_stops_before_the_next_sample() -> TestResult {
        let mut fixture = crate::test_support::validation_fixture(
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
}
