use crate::{
    BenchError, BoundTool, CaseId, ExperimentPreparation, ExperimentRequest, HostMetadata,
    MeasurementContract, MeasurementLock, Platform, ProcessRunner, ReportFormat, Role, RunBundle,
    RunEvent, RunManifest, RunOutcome, RunResult, RunWriter, Store, ToolIdentity, ToolPaths,
    measure_rss, measure_timing, prepare_experiment, publication_record, render,
    validate_experiment,
};

/// Selected local execution stages, with caller-identified measurement programs.
#[derive(Clone, Copy, Debug)]
pub enum RunMode<'a> {
    CheckOnly {
        engine: Option<&'a BoundTool>,
    },
    Measure {
        engine: &'a BoundTool,
        time: Option<&'a BoundTool>,
        platform: Platform,
    },
}
/// Full composition with explicit resources and a caller-held measurement lock.
pub struct RunRequest<'a> {
    pub preparation: ExperimentPreparation<'a>,
    /// Exact submitted text, if available; typed callers may leave it absent.
    pub submitted_toml: Option<&'a str>,
    pub measurement_lock: &'a MeasurementLock,
    pub harness: &'a BoundTool,
    pub host: &'a HostMetadata,
    pub mode: RunMode<'a>,
    pub experiment: Option<&'a ExperimentRequest>,
}
/// Compose preparation, correctness, timing, RSS and durable local reports.
/// Workload failures return a retained bundle with non-complete result; callers must inspect it.
/// # Errors
/// Returns storage/report errors when a complete failure bundle cannot be retained.
pub fn run(
    request: &RunRequest<'_>,
    store: &Store,
    runner: &ProcessRunner,
) -> Result<RunBundle, BenchError> {
    let suite = request.preparation.suite;
    let mut writer = if let Some(source) = request.submitted_toml {
        store.begin_resolved_run(source, suite, request.experiment)?
    } else if let Some(experiment) = request.experiment {
        store.begin_tagged_run(suite, experiment)?
    } else {
        store.begin_run(suite)?
    };
    let mut manifest = RunManifest {
        schema_version: 1,
        execution_kind: Some(match request.mode {
            RunMode::CheckOnly { .. } => crate::ExecutionKind::CheckOnly,
            RunMode::Measure { .. } => crate::ExecutionKind::Measure,
        }),
        run_id: writer.id().into(),
        contract: None,
        roles: std::collections::BTreeMap::new(),
        inputs: vec![],
        selected_cases: vec![],
        host: Some(request.host.clone()),
        tool_paths: None,
        experiment: request.experiment.cloned(),
    };
    let runner = runner.with_evidence(&writer.budget);
    let outcome = execute(request, store, &runner, &mut writer, &mut manifest);
    let result = RunResult {
        schema_version: 1,
        outcome: if outcome.is_ok() && matches!(request.mode, RunMode::Measure { time: None, .. }) {
            RunOutcome::Incomplete
        } else if outcome.is_ok() {
            RunOutcome::Complete
        } else {
            RunOutcome::Failed
        },
        message: outcome.err().map(|error| error.to_string()).or_else(|| {
            matches!(request.mode, RunMode::Measure { time: None, .. })
                .then(|| "requested RSS is unavailable".into())
        }),
    };
    finalize(writer, &manifest, result)
}
fn finalize(
    mut writer: RunWriter,
    manifest: &RunManifest,
    mut result: RunResult,
) -> Result<RunBundle, BenchError> {
    if result.outcome != RunOutcome::Complete {
        writer.budget.fail();
    }
    // Assemble reports in memory before accepting any projection bytes. If a normal
    // projection cannot fit, recompute it with the authoritative failed outcome.
    for attempt in 0..2 {
        let bundle = RunBundle {
            path: writer.path().into(),
            manifest: manifest.clone(),
            result: result.clone(),
            files: crate::store::inventory(writer.path())?,
        };
        let projections = (|| {
            let record = publication_record(&bundle)?;
            let json = serde_json::to_vec_pretty(&record)?;
            let mut markdown = Vec::new();
            render(&bundle, ReportFormat::Markdown, &mut markdown)?;
            writer.budget.ensure_capacity(crate::budget::sum_sizes(&[
                json.len(),
                markdown.len(),
                RunWriter::finalization_bytes(manifest, &result)?,
                failure_event_bytes(&result)?,
            ])?)?;
            Ok::<_, BenchError>((json, markdown))
        })();
        match projections {
            Ok((json, markdown)) => {
                if let Some(message) = &result.message {
                    writer.append_event(&RunEvent::Failure(message.clone()))?;
                }
                writer.write_bytes(&writer.path().join("publication.json"), &json)?;
                writer.write_bytes(&writer.path().join("report.md"), &markdown)?;
                return writer.finish(manifest, &result);
            }
            Err(error) => {
                result.outcome = RunOutcome::Failed;
                result.message = Some(format!(
                    "{}report finalization: {error}",
                    result
                        .message
                        .as_ref()
                        .map_or_else(String::new, |message| format!("{message}; "))
                ));
                writer.budget.fail();
                if attempt == 1 {
                    // Raw evidence remains sealed and inspectable even when a compact
                    // projection cannot fit inside the separate failure allowance.
                    writer.append_event(&RunEvent::Failure(
                        result.message.clone().unwrap_or_default(),
                    ))?;
                    return writer.finish(manifest, &result);
                }
            }
        }
    }
    Err(BenchError::Evidence(
        "unreachable finalization state".into(),
    ))
}
fn failure_event_bytes(result: &RunResult) -> Result<usize, BenchError> {
    result.message.as_ref().map_or(Ok(0), |message| {
        crate::budget::sum_sizes(&[
            serde_json::to_vec(&RunEvent::Failure(message.clone()))?.len(),
            1,
        ])
    })
}

fn execute(
    request: &RunRequest<'_>,
    store: &Store,
    runner: &ProcessRunner,
    writer: &mut RunWriter,
    manifest: &mut RunManifest,
) -> Result<(), BenchError> {
    writer.check_evidence_limit()?;
    crate::verify_file(&request.harness.path, &request.harness.identity.file)?;
    let prepared = prepare_experiment(
        request.measurement_lock,
        &request.preparation,
        store,
        runner,
    )?;
    let engine = match request.mode {
        RunMode::CheckOnly { engine } => engine,
        RunMode::Measure { engine, .. } => Some(engine),
    };
    if let Some(engine) = engine {
        crate::verify_file(&engine.path, &engine.identity.file)?;
        if engine.identity.version.trim() != "hyperfine 1.20.0" {
            return Err(BenchError::Execution(
                "only Hyperfine 1.20.0 is supported".into(),
            ));
        }
    }
    resolve_manifest(request, &prepared, engine, manifest)?;
    crate::bundle::capture_resources(&prepared, request, writer)?;
    if let RunMode::CheckOnly {
        engine: Some(engine),
    } = request.mode
    {
        let path = writer.path().join("contract-engine");
        std::fs::create_dir(&path)?;
        crate::timing::identify_engine(engine, &prepared, &path, runner)?;
    }
    writer.freeze_experiment(manifest)?;
    for case in prepared.cases() {
        if !case.equal_dataset_operands(prepared.profile(), prepared.roles().roles.keys().copied())
        {
            return Err(BenchError::Execution(
                "unequal dataset operands across roles".into(),
            ));
        }
    }
    let validated = validate_experiment(prepared, writer, runner)?;
    writer.check_evidence_limit()?;
    if let RunMode::Measure {
        engine,
        time,
        platform,
    } = request.mode
    {
        measure_timing(&validated, writer, runner, engine)?;
        if let Some(time) = time {
            measure_rss(&validated, writer, runner, time, platform)?;
        }
    }
    validated.revalidate()?;
    for case in validated.prepared().cases() {
        validated.final_case_check(
            &CaseId::new(case.id.clone())?,
            &writer.path().join(format!("final-{}", case.id)),
            runner,
        )?;
        writer.check_evidence_limit()?;
    }
    Ok(())
}
fn resolve_manifest(
    request: &RunRequest<'_>,
    prepared: &crate::PreparedExperiment<'_>,
    engine: Option<&BoundTool>,
    manifest: &mut RunManifest,
) -> Result<(), BenchError> {
    let generator = prepared
        .roles()
        .generator
        .as_ref()
        .ok_or_else(|| BenchError::Evidence("missing resolved generator".into()))?;
    manifest.roles = prepared
        .roles()
        .roles
        .iter()
        .map(|(role, executable)| (*role, executable.artifact.clone()))
        .collect();
    manifest.inputs = prepared.datasets().inputs.values().cloned().collect();
    manifest.selected_cases = prepared
        .cases()
        .iter()
        .map(|case| case.id.clone())
        .collect();
    manifest.contract = Some(resolved_contract(
        request.preparation.suite,
        request.preparation.profile,
        prepared.roles(),
        &request.harness.identity,
        engine.map(|tool| &tool.identity),
    )?);
    manifest.tool_paths = Some(ToolPaths {
        harness: request.harness.path.clone(),
        generator: generator.path.clone(),
        engine: engine.map(|tool| tool.path.clone()),
    });
    Ok(())
}

/// Shared resolution for a normal run and strict replay's pre-execution check.
pub fn resolved_contract(
    suite: &crate::Suite,
    profile: crate::MeasurementProfile,
    roles: &crate::RoleBindings,
    harness: &ToolIdentity,
    engine: Option<&ToolIdentity>,
) -> Result<MeasurementContract, BenchError> {
    let generator = roles
        .generator
        .as_ref()
        .ok_or_else(|| BenchError::Evidence("missing resolved generator".into()))?;
    Ok(MeasurementContract {
        schema_version: 1,
        suite: suite.clone(),
        harness: harness.clone(),
        generator: ToolIdentity {
            file: generator.artifact.file.clone(),
            version: "version unavailable; retained executable identified by SHA-256".into(),
        },
        engine: engine.cloned(),
        validator_policy: "correctness-v1".into(),
        analysis_policy: "descriptive-v1".into(),
        build: roles
            .roles
            .get(&Role::Previous)
            .or_else(|| roles.roles.get(&Role::Candidate))
            .and_then(|bound| bound.artifact.build.as_ref())
            .map(|build| build.policy.clone()),
        profile,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{rss_support, timing_support, validation_fixture};
    use crate::{ExecutableSource, MeasurementProfile, collect_host};
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    #[test]
    fn composes_all_role_combinations_and_expected_nonzero_status() -> TestResult {
        for selection in ["previous", "reference", "both"] {
            let mut fixture =
                validation_fixture("printf 'EFGH\\n'; exit 1", "printf 'EFGH\\n'; exit 1")?;
            fixture
                .suite
                .cases
                .first_mut()
                .ok_or("case")?
                .expected_status = 1;
            if selection != "previous" {
                fixture.request.reference = match fixture.request.previous.as_ref() {
                    Some(ExecutableSource::Prebuilt(path)) => Some(path.clone()),
                    _ => None,
                };
            }
            if selection == "reference" {
                fixture.request.previous = None;
            }
            let engine = timing_support::engine(&fixture, "")?;
            let time = rss_support::time_tool(&fixture, Platform::Linux, "")?;
            let host = collect_host(&fixture.suite.environment);
            let bundle = run(
                &RunRequest {
                    submitted_toml: None,
                    preparation: ExperimentPreparation {
                        run: &fixture.request,
                        suite: &fixture.suite,
                        profile: MeasurementProfile::Smoke,
                        selected_cases: &[],
                        expected_datasets: None,
                    },
                    measurement_lock: &fixture.measurement_lock,
                    harness: &engine,
                    host: &host,
                    mode: RunMode::Measure {
                        engine: &engine,
                        time: Some(&time),
                        platform: Platform::Linux,
                    },
                    experiment: None,
                },
                &fixture.store,
                &fixture.runner,
            )?;
            crate::test_support::equal(&(bundle.result.outcome), &(RunOutcome::Complete))?;
            let record = publication_record(&bundle)?;
            crate::test_support::equal(
                &(record.analysis.timing_samples.len()),
                &(if selection == "both" { 12 } else { 8 }),
            )?;
            crate::test_support::require(
                bundle.path.join("publication.json").is_file(),
                "runner assertion failed",
            )?;
            crate::test_support::require(
                bundle.path.join("report.md").is_file(),
                "runner assertion failed",
            )?;
            crate::test_support::require(record.analysis.smoke_only, "runner assertion failed")?;
            crate::test_support::equal(
                &(fixture
                    .store
                    .load_run(&bundle.manifest.run_id)?
                    .result
                    .outcome),
                &(RunOutcome::Complete),
            )?;
        }
        Ok(())
    }
    #[test]
    fn check_only_needs_no_timing_engine_and_failed_correctness_remains_failure() -> TestResult {
        for bad in [false, true] {
            let fixture = validation_fixture(
                if bad {
                    "printf 'wrong'"
                } else {
                    "printf 'EFGH\\n'"
                },
                "printf 'EFGH\\n'",
            )?;
            let harness = timing_support::engine(&fixture, "")?;
            let host = collect_host(&fixture.suite.environment);
            let bundle = run(
                &RunRequest {
                    submitted_toml: None,
                    preparation: ExperimentPreparation {
                        run: &fixture.request,
                        suite: &fixture.suite,
                        profile: MeasurementProfile::Full,
                        selected_cases: &[],
                        expected_datasets: None,
                    },
                    measurement_lock: &fixture.measurement_lock,
                    harness: &harness,
                    host: &host,
                    mode: RunMode::CheckOnly { engine: None },
                    experiment: None,
                },
                &fixture.store,
                &fixture.runner,
            )?;
            crate::test_support::equal(
                &(bundle.result.outcome),
                &(if bad {
                    RunOutcome::Failed
                } else {
                    RunOutcome::Complete
                }),
            )?;
            crate::test_support::equal(
                &serde_json::to_value(&bundle.manifest)?["execution_kind"],
                &serde_json::json!("check-only"),
            )?;
            crate::test_support::require(
                bundle
                    .manifest
                    .contract
                    .as_ref()
                    .is_some_and(|c| c.engine.is_none() && c.build.is_none()),
                "runner assertion failed",
            )?;
            crate::test_support::require(
                publication_record(&bundle)?
                    .analysis
                    .timing_samples
                    .is_empty(),
                "runner assertion failed",
            )?;
        }
        Ok(())
    }
    #[test]
    fn repeated_dataset_operands_are_rejected_before_the_correctness_gate() -> TestResult {
        let body = "for operand do case \"$operand\" in /*) while IFS= read -r line; do :; done < \"$operand\";; esac; done; printf 'EFGH\\n'";
        for equal in [false, true] {
            let mut fixture = validation_fixture(body, body)?;
            let case = fixture.suite.cases.first_mut().ok_or("case")?;
            case.role_argv.insert(
                Role::Candidate,
                vec![
                    "--different-option".into(),
                    "@input:tiny".into(),
                    "@input:tiny".into(),
                ],
            );
            if equal {
                case.argv.push("@input:tiny".into());
            }
            let engine = timing_support::engine(&fixture, "")?;
            let host = collect_host(&fixture.suite.environment);
            let bundle = run(
                &RunRequest {
                    submitted_toml: None,
                    preparation: ExperimentPreparation {
                        run: &fixture.request,
                        suite: &fixture.suite,
                        profile: MeasurementProfile::Full,
                        selected_cases: &[],
                        expected_datasets: None,
                    },
                    measurement_lock: &fixture.measurement_lock,
                    harness: &engine,
                    host: &host,
                    mode: RunMode::CheckOnly { engine: None },
                    experiment: None,
                },
                &fixture.store,
                &fixture.runner,
            )?;
            crate::test_support::equal(
                &bundle.result.outcome,
                &if equal {
                    RunOutcome::Complete
                } else {
                    RunOutcome::Failed
                },
            )?;
            if !equal {
                crate::test_support::require(
                    bundle
                        .result
                        .message
                        .as_ref()
                        .is_some_and(|message| message.contains("unequal dataset operands")),
                    "missing unequal-work failure",
                )?;
                crate::test_support::require(
                    !bundle.path.join("validation").exists(),
                    "unequal work entered correctness gate",
                )?;
            }
        }
        Ok(())
    }
    #[test]
    fn cumulative_correctness_budget_stops_children_and_seals_failure_metadata() -> TestResult {
        let mut fixture = validation_fixture("printf '%300s' x", "printf '%300s' x")?;
        let mut second = fixture.suite.cases.first().ok_or("case")?.clone();
        second.id = "second".into();
        fixture.suite.cases.push(second);
        fixture.suite.limits.max_stream_bytes = 10_000;
        let serialized = toml::to_string(&fixture.suite)?;
        let original_limit = u64::try_from(serialized.len() * 2 + 600)?;
        let engine = timing_support::engine(&fixture, "")?;
        let host = collect_host(&fixture.suite.environment);
        let prepared = fixture.prepare(MeasurementProfile::Full)?;
        let metadata_request = RunRequest {
            submitted_toml: None,
            preparation: ExperimentPreparation {
                run: &fixture.request,
                suite: &fixture.suite,
                profile: MeasurementProfile::Full,
                selected_cases: &[],
                expected_datasets: None,
            },
            measurement_lock: &fixture.measurement_lock,
            harness: &engine,
            host: &host,
            mode: RunMode::CheckOnly { engine: None },
            experiment: None,
        };
        let replay_metadata_bytes = serde_json::to_vec_pretty(&crate::bundle::resource_record(
            &prepared,
            &metadata_request,
        )?)?
        .len();
        // Preserve the existing correctness-capture allowance after the newly required metadata.
        fixture.suite.limits.max_evidence_bytes =
            original_limit + u64::try_from(replay_metadata_bytes)?;
        let bundle = run(
            &RunRequest {
                submitted_toml: None,
                preparation: ExperimentPreparation {
                    run: &fixture.request,
                    suite: &fixture.suite,
                    profile: MeasurementProfile::Full,
                    selected_cases: &[],
                    expected_datasets: None,
                },
                measurement_lock: &fixture.measurement_lock,
                harness: &engine,
                host: &host,
                mode: RunMode::CheckOnly { engine: None },
                experiment: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        crate::test_support::equal(&bundle.result.outcome, &RunOutcome::Failed)?;
        let captures: Vec<_> = bundle
            .files
            .iter()
            .filter(|(name, _)| name.ends_with(".stdout"))
            .collect();
        crate::test_support::require(
            !captures.is_empty() && captures.len() < 4,
            "correctness gate continued capturing after cap",
        )?;
        let captured_bytes: u64 = captures.iter().map(|(_, file)| file.bytes).sum();
        crate::test_support::require(
            captured_bytes < 1_200,
            "all correctness captures escaped budget",
        )?;
        let bytes = crate::budget::size(&bundle.path)?;
        let index = std::fs::metadata(bundle.path.join("checksums.json"))?.len();
        crate::test_support::require(
            bytes
                <= fixture.suite.limits.max_evidence_bytes
                    + crate::budget::FAILURE_METADATA_BYTES
                    + index,
            "failure finalization escaped approved reserve",
        )?;
        fixture.store.load_run(&bundle.manifest.run_id)?;
        crate::test_support::require(
            bundle.files.contains_key("validation/report.json"),
            "quota failure lost correctness observations",
        )?;
        crate::test_support::require(
            std::fs::read_to_string(bundle.path.join("validation/report.json"))?
                .contains("EvidenceLimit"),
            "missing budget diagnostic",
        )?;
        fixture.suite.limits.max_evidence_bytes = original_limit;
        assert_direct_stage_cap(&fixture)?;
        Ok(())
    }
    fn assert_direct_stage_cap(
        fixture: &crate::test_support::validation_support::Fixture,
    ) -> TestResult {
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let prepared = prepare_experiment(
            &fixture.measurement_lock,
            &ExperimentPreparation {
                run: &fixture.request,
                suite: &fixture.suite,
                profile: MeasurementProfile::Full,
                selected_cases: &[],
                expected_datasets: None,
            },
            &fixture.store,
            &fixture.runner,
        )?;
        crate::test_support::require(
            validate_experiment(prepared, &mut writer, &fixture.runner).is_err(),
            "direct stage escaped budget",
        )?;
        let bundle = writer.record_failure(RunOutcome::Failed, "direct stage quota")?;
        crate::test_support::require(
            bundle
                .files
                .keys()
                .filter(|name| name.ends_with(".stdout"))
                .count()
                < 4,
            "direct stage continued captures",
        )?;
        crate::test_support::require(
            bundle.files.contains_key("validation/report.json"),
            "direct stage lost observations",
        )?;
        Ok(())
    }
    #[test]
    fn full_metric_counts_and_missing_rss_are_retained_without_success() -> TestResult {
        for memory in [true, false] {
            let fixture = validation_fixture("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
            let engine = timing_support::engine(&fixture, "")?;
            let time = rss_support::time_tool(&fixture, Platform::Linux, "")?;
            let host = collect_host(&fixture.suite.environment);
            let mut bundle = run(
                &RunRequest {
                    submitted_toml: None,
                    preparation: ExperimentPreparation {
                        run: &fixture.request,
                        suite: &fixture.suite,
                        profile: MeasurementProfile::Full,
                        selected_cases: &[],
                        expected_datasets: None,
                    },
                    measurement_lock: &fixture.measurement_lock,
                    harness: &engine,
                    host: &host,
                    mode: RunMode::Measure {
                        engine: &engine,
                        time: memory.then_some(&time),
                        platform: Platform::Linux,
                    },
                    experiment: None,
                },
                &fixture.store,
                &fixture.runner,
            )?;
            crate::test_support::equal(
                &bundle.result.outcome,
                &if memory {
                    RunOutcome::Complete
                } else {
                    RunOutcome::Incomplete
                },
            )?;
            let record = publication_record(&bundle)?;
            crate::test_support::equal(
                &serde_json::to_value(&record.manifest)?["execution_kind"],
                &serde_json::json!("measure"),
            )?;
            crate::test_support::equal(&record.analysis.timing_samples.len(), &80)?;
            crate::test_support::equal(
                &record.analysis.rss_samples.len(),
                &if memory { 10 } else { 0 },
            )?;
            // Selecting reversed roles must retain incomplete/failed suppression even with full samples.
            if memory {
                bundle.result.outcome = RunOutcome::Failed;
            }
            let selected = crate::comparison_record(
                &bundle,
                crate::ComparisonSelection {
                    baseline: crate::Role::Candidate,
                    candidate: crate::Role::Previous,
                },
            )?;
            crate::test_support::require(
                selected.analysis.cases.iter().all(|case| {
                    case.comparisons.iter().all(|comparison| {
                        comparison.ratio.is_none()
                            && comparison.batch_ratios.is_empty()
                            && comparison.direction == crate::Direction::Unavailable
                    })
                }),
                "selected failed comparison exposed a ratio",
            )?;
            crate::render_record(&selected, crate::ReportFormat::Json, &mut Vec::new())?;
        }
        Ok(())
    }
    #[test]
    fn tagged_check_freezes_verified_contract_and_changed_work_retains_failed_attempt() -> TestResult
    {
        let build = crate::test_support::build::Fixture::new()?;
        let mut fixture = validation_fixture("printf 'first\\n'", "printf 'first\\n'")?;
        fixture.suite.package = "tiny".into();
        fixture.suite.binary = "tiny".into();
        let case = fixture.suite.cases.first_mut().ok_or("case")?;
        case.argv.clear();
        case.correctness = vec![
            crate::CorrectnessRule::Literal {
                stream: crate::Stream::Stdout,
                text: "first\n".into(),
            },
            crate::CorrectnessRule::EmptyStderr {},
        ];
        fixture.request.repository.clone_from(&build.repo);
        fixture.request.previous = Some(ExecutableSource::Revision("HEAD".into()));
        fixture.request.git = Some(build.git.clone());
        fixture.request.tools = Some(build.tools.clone());
        let engine = timing_support::engine(&fixture, "")?;
        let host = collect_host(&fixture.suite.environment);
        let experiment = ExperimentRequest {
            id: "attempts".into(),
            hypothesis: "saved hypothesis".into(),
            change_summary: "saved change".into(),
            requested_previous: "HEAD".into(),
            requested_candidate: "prebuilt".into(),
        };
        let runner = crate::test_support::build::runner();
        for changed in [false, true] {
            if changed {
                fixture.suite.cases.first_mut().ok_or("case")?.work = Some(crate::Work {
                    amount: 9,
                    unit: crate::WorkUnit::Bytes,
                });
            }
            let bundle = run(
                &RunRequest {
                    submitted_toml: None,
                    preparation: ExperimentPreparation {
                        run: &fixture.request,
                        suite: &fixture.suite,
                        profile: MeasurementProfile::Smoke,
                        selected_cases: &[],
                        expected_datasets: None,
                    },
                    measurement_lock: &fixture.measurement_lock,
                    harness: &engine,
                    host: &host,
                    mode: RunMode::CheckOnly {
                        engine: Some(&engine),
                    },
                    experiment: Some(&experiment),
                },
                &fixture.store,
                &runner,
            )?;
            if !changed {
                reject_manifest_changed_after_preflight(&fixture, &experiment, &bundle)?;
                reject_changed_replay_anchor(&fixture, &experiment, &bundle)?;
            }
            let history = crate::history_record(&bundle)?;
            crate::test_support::equal(&history.publication.result, &bundle.result)?;
            let bindings =
                crate::replay_bindings(&bundle, &fixture.request.home, &fixture.request.config)?;
            crate::test_support::equal(
                &crate::replay_request(&bundle, &bindings).is_err(),
                &changed,
            )?;
            crate::test_support::equal(
                &bundle.result.outcome,
                &if changed {
                    RunOutcome::Failed
                } else {
                    RunOutcome::Complete
                },
            )?;
            crate::test_support::require(
                publication_record(&bundle)?
                    .analysis
                    .timing_samples
                    .is_empty(),
                "tagged check measured",
            )?;
            crate::test_support::require(
                bundle
                    .path
                    .join("contract-engine/engine-version.stdout")
                    .exists(),
                "tagged check did not identify engine",
            )?;
        }
        crate::test_support::equal(
            &std::fs::read_dir(fixture.store.root().join("experiments/attempts/attempts"))?.count(),
            &3,
        )?;
        Ok(())
    }
    fn reject_changed_replay_anchor(
        fixture: &crate::test_support::validation_support::Fixture,
        experiment: &ExperimentRequest,
        bundle: &RunBundle,
    ) -> TestResult {
        let path = fixture
            .store
            .root()
            .join("experiments")
            .join(&experiment.id)
            .join("experiment.json");
        let bytes = std::fs::read(&path)?;
        let mut anchor: crate::ExperimentRecord = serde_json::from_slice(&bytes)?;
        anchor.starting_sha = "0".repeat(40);
        crate::store::atomic_json(&path, &anchor)?;
        let bindings =
            crate::replay_bindings(bundle, &fixture.request.home, &fixture.request.config)?;
        let rejected = crate::replay_request(bundle, &bindings).is_err();
        std::fs::write(&path, bytes)?;
        crate::test_support::require(rejected, "strict replay replaced frozen starting SHA")?;
        Ok(())
    }
    fn reject_manifest_changed_after_preflight(
        fixture: &crate::test_support::validation_support::Fixture,
        experiment: &ExperimentRequest,
        bundle: &RunBundle,
    ) -> TestResult {
        let mut writer = fixture.store.begin_tagged_run(&fixture.suite, experiment)?;
        let mut manifest = bundle.manifest.clone();
        manifest.run_id = writer.id().into();
        writer.freeze_experiment(&manifest)?;
        let previous = manifest.roles.get(&Role::Previous).ok_or("previous")?;
        let source = fixture.store.artifact_path(previous);
        let mut build = previous.build.clone().ok_or("build")?;
        build.policy.compiler = "different compiler after preflight".into();
        manifest.roles.insert(
            Role::Previous,
            crate::register_binary(&source, Some(build), &fixture.store)?,
        );
        let result = RunResult {
            schema_version: 1,
            outcome: RunOutcome::Failed,
            message: Some("late failure".into()),
        };
        crate::test_support::require(
            writer.finish(&manifest, &result).is_err(),
            "preflight marker accepted an unrelated attempted manifest",
        )?;
        Ok(())
    }
}
