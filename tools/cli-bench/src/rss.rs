use crate::{BenchError, BoundTool, ProcessRunner, RunWriter, ValidatedExperiment};

/// Native output syntax selected explicitly by the caller.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Platform {
    Linux,
    Darwin,
}
/// Unit retained alongside normalized bytes.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RssUnit {
    Kibibytes,
    Bytes,
}
/// One OS-accounted command peak, never a sum of pipeline process peaks.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct NativeRss {
    pub platform: Platform,
    pub value: u64,
    pub unit: RssUnit,
    pub bytes: u64,
    pub accounting_scope: String,
}
/// Independent memory observation; all raw paths are relative to the run root.
#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RssSample {
    pub case: String,
    pub role: crate::Role,
    pub ordinal: u32,
    pub rss: NativeRss,
    pub invocation_scope: String,
    pub status: i32,
    pub raw_resource: std::path::PathBuf,
    /// Present only for a captured sink; drained/discarded/file sinks keep their boundary.
    pub raw_stdout: Option<std::path::PathBuf>,
    pub raw_stderr: std::path::PathBuf,
    pub target_stderr: std::path::PathBuf,
}
/// Parse one native peak; missing, duplicate and invalid values are errors.
/// # Errors
/// Rejects malformed output or values that cannot be normalized to bytes.
pub fn parse_peak_rss(platform: Platform, output: &[u8]) -> Result<NativeRss, BenchError> {
    let text =
        std::str::from_utf8(output).map_err(|_| failure("non-UTF-8 native resource output"))?;
    let mut value = None;
    for line in text.lines().map(str::trim) {
        let field = match platform {
            Platform::Linux if line.contains("Maximum resident set size") => Some(
                line.strip_prefix("Maximum resident set size (kbytes):")
                    .ok_or_else(|| failure("malformed GNU RSS field"))?
                    .trim(),
            ),
            Platform::Darwin if line.contains("maximum resident set size") => Some(
                line.strip_suffix("maximum resident set size")
                    .ok_or_else(|| failure("malformed Darwin RSS field"))?
                    .trim(),
            ),
            _ => None,
        };
        if let Some(field) = field {
            if value.is_some() {
                return Err(failure("duplicate RSS field"));
            }
            value = Some(decimal(field)?);
        }
    }
    let value = value.ok_or_else(|| failure("missing native RSS field"))?;
    let (unit, multiplier) = match platform {
        Platform::Linux => (RssUnit::Kibibytes, 1024),
        Platform::Darwin => (RssUnit::Bytes, 1),
    };
    Ok(NativeRss {
        platform, value, unit,
        bytes: value.checked_mul(multiplier).ok_or_else(|| failure("RSS byte conversion overflow"))?,
        accounting_scope: "native OS command accounting; descendant accounting is OS-dependent; never a sum of pipeline peaks or an allocation count".into(),
    })
}
fn decimal(value: &str) -> Result<u64, BenchError> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(failure(
            "native resource value must be an unsigned decimal integer",
        ));
    }
    value
        .parse()
        .map_err(|_| failure("native resource value overflow"))
}
/// Sample fresh native-time commands separately from elapsed-time samples.
///
/// Size evidence is copied once from each immutable artifact after final verification.
/// The successful capability borrows the session lock for the entire call.
/// # Errors
/// Rejects tool/target failures, changed identities and final correctness failures.
pub fn measure_rss(
    validated: &ValidatedExperiment<'_>,
    writer: &mut RunWriter,
    runner: &ProcessRunner,
    tool: &BoundTool,
    platform: Platform,
) -> Result<Vec<RssSample>, BenchError> {
    let prepared = validated.prepared();
    if !writer.matches_suite(prepared.suite()) {
        return Err(failure("RSS writer suite differs from validated suite"));
    }
    validated.revalidate()?;
    crate::verify_file(&tool.path, &tool.identity.file)?;
    let limits = &prepared.suite().limits;
    let runner = runner.bounded(
        std::time::Duration::from_secs(limits.sample_timeout_seconds),
        limits.max_stream_bytes,
    );
    let raw = writer.path().join("raw/rss");
    if std::fs::read_dir(&raw)?.next().is_some() {
        return Err(failure("RSS evidence already exists; begin a fresh run"));
    }
    crate::store::atomic_json(
        &raw.join("tool.json"),
        &serde_json::json!({"tool": tool.identity, "path": tool.path, "platform": platform}),
    )?;
    let mut samples = vec![];
    for case in prepared.cases() {
        let id = crate::CaseId::new(case.id.clone())?;
        for role in validated.roles().roles.keys().copied() {
            for ordinal in 1..=prepared.profile().policy().rss_samples {
                samples.push(execute_sample(
                    validated,
                    writer,
                    &runner,
                    tool,
                    platform,
                    case,
                    (role, ordinal),
                )?);
            }
        }
        validated.final_case_check(&id, &raw.join(format!("final-{}", case.id)), &runner)?;
        writer.append_event(&crate::RunEvent::Stage(format!(
            "RSS and final correctness passed for {}",
            case.id
        )))?;
    }
    validated.revalidate()?;
    crate::verify_file(&tool.path, &tool.identity.file)?;
    let sizes: std::collections::BTreeMap<_, _> = validated
        .roles()
        .roles
        .iter()
        .map(|(role, executable)| (*role, &executable.artifact.file))
        .collect();
    crate::store::atomic_json(&raw.join("executable-sizes.json"), &sizes)?;
    crate::store::atomic_json(&raw.join("samples.json"), &samples)?;
    Ok(samples)
}
fn execute_sample(
    validated: &ValidatedExperiment<'_>,
    writer: &RunWriter,
    runner: &ProcessRunner,
    tool: &BoundTool,
    platform: Platform,
    case: &crate::CaseSpec,
    (role, ordinal): (crate::Role, u32),
) -> Result<RssSample, BenchError> {
    let prepared = validated.prepared();
    let id = crate::CaseId::new(case.id.clone())?;
    let scratch = prepared
        .scratch(&id)
        .ok_or_else(|| failure("missing validated scratch"))?;
    validated.revalidate()?;
    crate::verify_file(&tool.path, &tool.identity.file)?;
    crate::reset_case(case, validated.datasets(), scratch)?;
    let invocation = crate::resolve_invocation(
        case,
        role,
        prepared.profile(),
        validated.roles(),
        validated.datasets(),
        scratch.path(),
    )?;
    let raw = writer.path().join("raw/rss");
    let name = format!("{}-{role:?}-{ordinal}", case.id);
    let paths = crate::CapturePaths {
        stdout: raw.join(format!("{name}.stdout")),
        stderr: raw.join(format!("{name}.stderr")),
    };
    let resource = raw.join(format!("{name}.resource"));
    let target_stderr = raw.join(format!("{name}.target-stderr"));
    let mut command = invocation.command.clone();
    let mut argv = match platform {
        Platform::Linux => vec![
            "-v".into(),
            "-o".into(),
            crate::process::utf8_path(&resource)?.into(),
            "--".into(),
        ],
        Platform::Darwin => vec!["-l".into()],
    };
    argv.push(crate::process::utf8_path(&command.program)?.into());
    argv.extend(command.argv);
    command.program.clone_from(&tool.path);
    command.argv = argv;
    crate::store::atomic_json(
        &raw.join(format!("{name}.command.json")),
        &serde_json::json!({"tool": tool.identity, "argv": command.argv, "scope": format!("{:?}", invocation.scope), "stdin": format!("{:?}", command.stdin), "stdout": format!("{:?}", command.stdout), "expected_status": invocation.expected_status}),
    )?;
    let outcome = match platform {
        Platform::Linux => runner.execute_with_file_limit(
            &command,
            &paths,
            &crate::OutputFileLimit {
                path: resource.clone(),
                max_bytes: prepared.suite().limits.max_stream_bytes,
            },
        )?,
        Platform::Darwin => runner.execute(&command, &paths)?,
    };
    crate::store::atomic_json(
        &raw.join(format!("{name}.outcome.json")),
        &serde_json::json!({"status": format!("{:?}", outcome.status), "stopped": format!("{:?}", outcome.stopped)}),
    )?;
    let status = outcome.check_expected(invocation.expected_status);
    let effects = validated.verify_effects(&id, role);
    status?;
    effects?;
    retain_diagnostics(
        validated,
        case,
        role,
        platform,
        &paths.stderr,
        &resource,
        &target_stderr,
    )?;
    let output = std::fs::read(&resource)?;
    check_resource_trailer(platform, &output, invocation.expected_status)?;
    let sample = RssSample {
        case: case.id.clone(),
        role,
        ordinal,
        rss: parse_peak_rss(platform, &output)?,
        invocation_scope: format!("{:?}", invocation.scope),
        status: invocation.expected_status,
        raw_resource: relative(writer, &resource)?,
        raw_stdout: if matches!(command.stdout, crate::CommandOutput::Capture) {
            Some(relative(writer, &paths.stdout)?)
        } else {
            None
        },
        raw_stderr: relative(writer, &paths.stderr)?,
        target_stderr: relative(writer, &target_stderr)?,
    };
    crate::store::atomic_json(&raw.join(format!("{name}.sample.json")), &sample)?;
    Ok(sample)
}
fn retain_diagnostics(
    validated: &ValidatedExperiment<'_>,
    case: &crate::CaseSpec,
    role: crate::Role,
    platform: Platform,
    raw_stderr: &std::path::Path,
    resource: &std::path::Path,
    target_stderr: &std::path::Path,
) -> Result<(), BenchError> {
    let diagnostic = validated
        .report()
        .observations
        .iter()
        .find(|observation| {
            observation.case == case.id
                && observation.role == role
                && observation.boundary == "declared-sink"
        })
        .ok_or_else(|| failure("missing validated target diagnostics"))?;
    let stderr = std::fs::read(raw_stderr)?;
    let (target, trailer) = match platform {
        Platform::Linux => (stderr.as_slice(), None),
        Platform::Darwin => {
            let prefix = usize::try_from(diagnostic.stderr.bytes)
                .map_err(|_| failure("diagnostic length overflow"))?;
            if prefix > stderr.len() {
                return Err(failure("missing target diagnostic prefix"));
            }
            let (target, trailer) = stderr.split_at(prefix);
            (target, Some(trailer))
        }
    };
    std::fs::write(target_stderr, target)?;
    crate::verify_file(target_stderr, &diagnostic.stderr)?;
    if platform == Platform::Darwin
        && target
            .windows(b"time: command terminated abnormally".len())
            .any(|bytes| bytes == b"time: command terminated abnormally")
    {
        return Err(failure(
            "Darwin target diagnostics are ambiguous with time's termination warning; RSS unavailable",
        ));
    }
    if let Some(trailer) = trailer {
        std::fs::write(resource, trailer)?;
    }
    Ok(())
}
fn check_resource_trailer(
    platform: Platform,
    output: &[u8],
    expected: i32,
) -> Result<(), BenchError> {
    let text = std::str::from_utf8(output).map_err(|_| failure("non-UTF-8 resource trailer"))?;
    match platform {
        Platform::Linux => {
            if text
                .lines()
                .any(|line| line.trim().starts_with("Command terminated by signal"))
            {
                return Err(failure("GNU time target terminated by signal"));
            }
            let statuses: Vec<_> = text
                .lines()
                .filter_map(|line| line.trim().strip_prefix("Exit status: "))
                .collect();
            let [status] = statuses.as_slice() else {
                return Err(failure("GNU time must report exactly one target status"));
            };
            if decimal(status)?
                != u64::try_from(expected).map_err(|_| failure("invalid expected status"))?
            {
                return Err(failure(
                    "GNU time target status differs from declared status",
                ));
            }
        }
        Platform::Darwin => {
            let mut lines = text.lines();
            let header: Vec<_> = lines
                .next()
                .ok_or_else(|| failure("missing Darwin time header"))?
                .split_whitespace()
                .collect();
            let [real, "real", user, "user", sys, "sys"] = header.as_slice() else {
                return Err(failure(
                    "unexpected Darwin time header or target termination warning",
                ));
            };
            for value in [real, user, sys] {
                let seconds: f64 = value
                    .parse()
                    .map_err(|_| failure("malformed Darwin time header"))?;
                if !seconds.is_finite() || seconds < 0.0 {
                    return Err(failure("invalid Darwin time header"));
                }
            }
            for line in lines {
                let line = line.trim();
                let (value, label) = line
                    .split_once(char::is_whitespace)
                    .ok_or_else(|| failure("malformed Darwin resource trailer"))?;
                decimal(value)?;
                if label.trim().is_empty() {
                    return Err(failure("missing Darwin resource label"));
                }
            }
        }
    }
    Ok(())
}
fn relative(writer: &RunWriter, path: &std::path::Path) -> Result<std::path::PathBuf, BenchError> {
    path.strip_prefix(writer.path())
        .map(std::path::Path::to_path_buf)
        .map_err(|_| failure("raw RSS evidence escaped run"))
}
fn failure(message: &str) -> BenchError {
    BenchError::Execution(message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{require, rss_support::time_tool, validation_support::TestResult};
    use crate::*;

    #[test]
    fn exact_native_units_and_fixtures() -> TestResult {
        let gnu = parse_peak_rss(
            Platform::Linux,
            include_bytes!("../tests/inputs/gnu-time-v.txt"),
        )?;
        require(
            (gnu.value, gnu.unit, gnu.bytes) == (1024, RssUnit::Kibibytes, 1_048_576),
            "incorrect GNU native unit normalization",
        )?;
        let darwin = parse_peak_rss(Platform::Darwin, b"1024 maximum resident set size\n")?;
        require(
            (darwin.value, darwin.unit, darwin.bytes) == (1024, RssUnit::Bytes, 1024),
            "incorrect Darwin native unit normalization",
        )?;
        require(
            parse_peak_rss(
                Platform::Darwin,
                include_bytes!("../tests/inputs/darwin-time-l.stderr"),
            )
            .is_ok(),
            "native Darwin fixture rejected",
        )?;
        Ok(())
    }
    #[test]
    fn rejects_missing_duplicate_malformed_negative_and_overflow() {
        for platform in [Platform::Linux, Platform::Darwin] {
            let line = |value: &str| match platform {
                Platform::Linux => format!("Maximum resident set size (kbytes): {value}\n"),
                Platform::Darwin => format!("{value} maximum resident set size\n"),
            };
            for output in [
                String::new(),
                line("-1"),
                line("wat"),
                line("1.0"),
                line("+1"),
                line("18446744073709551616"),
                format!("{}{}", line("1"), line("2")),
            ] {
                assert!(
                    parse_peak_rss(platform, output.as_bytes()).is_err(),
                    "accepted {output}"
                );
            }
        }
        assert!(
            parse_peak_rss(
                Platform::Linux,
                b"Maximum resident set size (kbytes): 18446744073709551615\n"
            )
            .is_err()
        );
    }
    #[test]
    fn full_and_smoke_reset_every_role_sample_and_keep_diagnostics() -> TestResult {
        for platform in [Platform::Linux, Platform::Darwin] {
            for (profile, count) in [
                (MeasurementProfile::Full, 5),
                (MeasurementProfile::Smoke, 1),
            ] {
                let mut fixture = crate::test_support::validation_fixture(
                    "/bin/mkdir -m 700 \"$1\"; printf 'target diagnostic' >&2",
                    "/bin/mkdir -m 700 \"$1\"; printf 'target diagnostic' >&2",
                )?;
                let case = fixture.suite.cases.first_mut().ok_or("case")?;
                case.argv = vec!["@scratch:dir".into()];
                case.correctness = vec![
                    CorrectnessRule::DirectoryTree {
                        paths: vec!["dir".into()],
                        compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
                    },
                    CorrectnessRule::Literal {
                        stream: Stream::Stderr,
                        text: "target diagnostic".into(),
                    },
                ];
                let mut second = case.clone();
                second.id = "second".into();
                fixture.suite.cases.push(second);
                fixture.request.reference = Some(crate::test_support::validation_support::script(
                    fixture.root.path(),
                    "reference",
                    "/bin/mkdir -m 700 \"$1\"; printf 'target diagnostic' >&2",
                )?);
                let tool = time_tool(&fixture, platform, "")?;
                let mut writer = fixture.store.begin_run(&fixture.suite)?;
                let validated =
                    validate_experiment(fixture.prepare(profile)?, &mut writer, &fixture.runner)?;
                let samples =
                    measure_rss(&validated, &mut writer, &fixture.runner, &tool, platform)?;
                require(samples.len() == 6 * count, "wrong independent RSS count")?;
                for case in ["last-line", "second"] {
                    for role in [Role::Reference, Role::Previous, Role::Candidate] {
                        require(
                            samples
                                .iter()
                                .filter(|s| s.case == case && s.role == role)
                                .map(|s| s.ordinal)
                                .collect::<Vec<_>>()
                                == (1..=u32::try_from(count)?).collect::<Vec<_>>(),
                            "wrong per-case/role RSS ordinals",
                        )?;
                    }
                }
                for sample in samples {
                    require(
                        std::fs::read(writer.path().join(sample.target_stderr))?
                            == b"target diagnostic",
                        "target diagnostic prefix changed",
                    )?;
                    require(
                        writer.path().join(sample.raw_resource).is_file(),
                        "raw resource missing",
                    )?;
                }
                require(
                    writer
                        .path()
                        .join("raw/rss/final-last-line/report.json")
                        .is_file(),
                    "final gate missing",
                )?;
                let sizes: std::collections::BTreeMap<Role, FileIdentity> = serde_json::from_slice(
                    &std::fs::read(writer.path().join("raw/rss/executable-sizes.json"))?,
                )?;
                for (role, executable) in &validated.roles().roles {
                    require(
                        sizes.get(role) == Some(&executable.artifact.file),
                        "size differs from immutable artifact",
                    )?;
                }
                require(
                    measure_rss(&validated, &mut writer, &fixture.runner, &tool, platform).is_err(),
                    "overwrote evidence",
                )?;
            }
        }
        Ok(())
    }
    #[test]
    fn rejects_wrong_status_diagnostic_prefix_and_abnormal_wrapper_warning() -> TestResult {
        for platform in [Platform::Linux, Platform::Darwin] {
            for mode in [
                "status=0",
                "printf 'unexpected' >&2",
                "printf 'time: command terminated abnormally\\n' >&2",
            ] {
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
                let tool = time_tool(&fixture, platform, mode)?;
                let mut writer = fixture.store.begin_run(&fixture.suite)?;
                let validated = validate_experiment(
                    fixture.prepare(MeasurementProfile::Smoke)?,
                    &mut writer,
                    &fixture.runner,
                )?;
                require(
                    measure_rss(&validated, &mut writer, &fixture.runner, &tool, platform).is_err(),
                    "bad wrapper accepted",
                )?;
                require(
                    !writer.path().join("raw/rss/samples.json").exists(),
                    "failed aggregate published",
                )?;
            }
        }
        Ok(())
    }
    #[test]
    fn unavailable_time_tool_fails() -> TestResult {
        let fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let tool = time_tool(&fixture, Platform::Darwin, "")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        std::fs::remove_file(&tool.path)?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Darwin,
            )
            .is_err(),
            "missing tool accepted",
        )?;
        Ok(())
    }
    #[test]
    fn darwin_target_warning_is_explicitly_ambiguous() -> TestResult {
        let mut fixture = crate::test_support::validation_fixture(
            "printf 'time: command terminated abnormally\\n' >&2",
            "printf 'time: command terminated abnormally\\n' >&2",
        )?;
        fixture.suite.cases.first_mut().ok_or("case")?.correctness =
            vec![CorrectnessRule::Literal {
                stream: Stream::Stderr,
                text: "time: command terminated abnormally\n".into(),
            }];
        let tool = time_tool(&fixture, Platform::Darwin, "")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Darwin,
            )
            .is_err(),
            "ambiguous native warning accepted",
        )?;
        Ok(())
    }
    #[test]
    fn rejects_gnu_signal_and_target_status_even_when_wrapper_status_matches() -> TestResult {
        require(check_resource_trailer(Platform::Linux, b"Command terminated by signal 15\nMaximum resident set size (kbytes): 1024\nExit status: 0\n", 0).is_err(), "signal accepted as zero")?;
        let fixture = crate::test_support::validation_fixture(
            r#"n=0
if [ -f "$HOME/count" ]; then read -r n < "$HOME/count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/count"
printf 'EFGH\n'
if [ "$n" -eq 3 ]; then exit 1; fi"#,
            "printf 'EFGH\\n'",
        )?;
        let tool = time_tool(&fixture, Platform::Linux, "trap 'exit 0' EXIT")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Linux,
            )
            .is_err(),
            "GNU nonzero target hidden by wrapper zero",
        )?;
        let outcome = std::fs::read_to_string(
            writer
                .path()
                .join("raw/rss/last-line-Candidate-1.outcome.json"),
        )?;
        require(
            outcome.contains("Exit(0)"),
            "fixture failed to mask wrapper status",
        )?;
        Ok(())
    }
    #[test]
    fn mutation_effect_failure_stops_and_final_output_drift_is_retained() -> TestResult {
        let mut fixture = crate::test_support::validation_fixture(
            r#"n=0
if [ -f "$HOME/count" ]; then read -r n < "$HOME/count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/count"
if [ "$n" -le 2 ]; then /bin/mkdir -m 700 "$1"; fi"#,
            "/bin/mkdir -m 700 \"$1\"",
        )?;
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv = vec!["@scratch:dir".into()];
        case.correctness = vec![CorrectnessRule::DirectoryTree {
            paths: vec!["dir".into()],
            compare_mode_to: Some(ComparisonTarget::SelectedBaselines),
        }];
        let tool = time_tool(&fixture, Platform::Darwin, "")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Full)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Darwin,
            )
            .is_err(),
            "missing mutation accepted",
        )?;
        require(
            std::fs::read_to_string(fixture.request.home.join("count"))?.trim() == "3",
            "continued after missing effect",
        )?;
        let fixture = crate::test_support::validation_fixture(
            r#"n=0
if [ -f "$HOME/count" ]; then read -r n < "$HOME/count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/count"
if [ "$n" -le 3 ]; then printf 'EFGH\n'; else printf 'WRONG\n'; fi"#,
            "printf 'EFGH\\n'",
        )?;
        let tool = time_tool(&fixture, Platform::Linux, "")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Linux,
            )
            .is_err(),
            "final output drift accepted",
        )?;
        let report: ValidationReport = serde_json::from_slice(&std::fs::read(
            writer.path().join("raw/rss/final-last-line/report.json"),
        )?)?;
        require(!report.passed(), "final failed checks missing")?;
        Ok(())
    }
    #[test]
    fn executable_byte_or_hash_drift_prevents_size_and_sample_publication() -> TestResult {
        let fixture =
            crate::test_support::validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
        let tool = time_tool(&fixture, Platform::Linux, "printf '#changed\\n' >> \"$1\"")?;
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        require(
            measure_rss(
                &validated,
                &mut writer,
                &fixture.runner,
                &tool,
                Platform::Linux,
            )
            .is_err(),
            "changed executable accepted",
        )?;
        for path in ["samples.json", "executable-sizes.json"] {
            require(
                !writer.path().join("raw/rss").join(path).exists(),
                "changed identity published aggregate",
            )?;
        }
        Ok(())
    }
}
