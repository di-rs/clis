use crate::{Analysis, BenchError, RunBundle, RunManifest, RunResult};
use serde::{Deserialize, Serialize};
use std::io::Write;

/// Output selection independent of terminal/global state. All text output is plain.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReportFormat {
    Terminal,
    Json,
    Markdown,
}
/// Compact local publication data; remote retention/URLs are assigned by the publisher.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PublicationRecord {
    pub schema_version: u32,
    #[serde(default)]
    pub comparison: Option<crate::ComparisonSelection>,
    pub manifest: RunManifest,
    pub result: RunResult,
    pub analysis: Analysis,
    pub validation: Option<crate::ValidationReport>,
    pub evidence: Vec<String>,
    pub attempt: Option<String>,
    pub replay: String,
    pub requested_retention_days: u32,
    pub expires_at_unix_seconds: Option<u64>,
}
/// Produce the versioned compact projection from retained evidence, without executing tools.
/// # Errors
/// Propagates invalid samples, changed evidence, or a projection exceeding 16 MiB.
pub fn publication_record(bundle: &RunBundle) -> Result<PublicationRecord, BenchError> {
    let timing = read_samples(bundle, "raw/timing/")?;
    let rss = read_samples(bundle, "raw/rss/")?;
    let validation = if bundle.files.contains_key("validation/report.json") {
        Some(read_evidence(bundle, "validation/report.json")?)
    } else {
        None
    };
    let mut analysis = crate::analyze(&bundle.manifest, &timing, &rss)?;
    suppress_failed(&mut analysis, bundle.result.outcome);
    let mut record = PublicationRecord {
        schema_version: 1,
        comparison: None,
        manifest: bundle.manifest.clone(),
        result: bundle.result.clone(),
        analysis,
        validation,
        evidence: bundle
            .files
            .keys()
            .filter(|name| {
                name.starts_with("raw/")
                    || name.starts_with("validation/")
                    || matches!(name.as_str(), "suite.toml" | "resolved-suite.toml")
            })
            .cloned()
            .collect(),
        attempt: bundle.manifest.experiment.as_ref().map(|experiment| {
            if crate::bundle::portable_root(bundle).is_some() {
                "../experiment/attempt.json".into()
            } else {
                format!(
                    "../../experiments/{}/attempts/{}.json",
                    experiment.id, bundle.manifest.run_id
                )
            }
        }),
        replay: format!(
            "cli-bench replay -i {}",
            shell_words::quote(crate::process::utf8_path(
                crate::bundle::portable_root(bundle).unwrap_or(&bundle.path)
            )?)
        ),
        requested_retention_days: 90,
        expires_at_unix_seconds: None,
    };
    if bundle.files.contains_key("publication.json") && bundle.files.contains_key("result.json") {
        let saved: PublicationRecord = read_evidence(bundle, "publication.json")?;
        if saved.manifest == bundle.manifest && saved.result == bundle.result {
            record.requested_retention_days = saved.requested_retention_days;
            record.expires_at_unix_seconds = saved.expires_at_unix_seconds;
        }
    }
    if serde_json::to_vec(&record)?.len() > 16_777_216 {
        return Err(BenchError::Evidence("publication exceeds 16 MiB".into()));
    }
    Ok(record)
}
/// Select two original role identities within one saved run.
/// # Errors
/// Rejects unavailable/equal roles, invalid observations and unreadable evidence.
pub fn comparison_record(
    bundle: &RunBundle,
    selection: crate::ComparisonSelection,
) -> Result<PublicationRecord, BenchError> {
    let mut record = publication_record(bundle)?;
    crate::analysis::select_comparison(&mut record.analysis, &record.manifest, selection)?;
    suppress_failed(&mut record.analysis, record.result.outcome);
    record.comparison = Some(selection);
    Ok(record)
}
fn suppress_failed(analysis: &mut Analysis, outcome: crate::RunOutcome) {
    if outcome != crate::RunOutcome::Complete {
        let issue =
            "run did not complete; ratios and directional conclusions suppressed".to_string();
        if !analysis.issues.contains(&issue) {
            analysis.issues.push(issue);
        }
        for comparison in analysis
            .cases
            .iter_mut()
            .flat_map(|case| &mut case.comparisons)
        {
            comparison.ratio = None;
            comparison.elapsed_change_seconds = None;
            comparison.elapsed_change_percent = None;
            comparison.batch_ratios.clear();
            comparison.direction = crate::Direction::Unavailable;
        }
    }
}
fn read_samples<T: serde::de::DeserializeOwned>(
    bundle: &RunBundle,
    prefix: &str,
) -> Result<Vec<T>, BenchError> {
    bundle
        .files
        .keys()
        .filter(|name| name.starts_with(prefix) && name.ends_with(".sample.json"))
        .map(|name| read_evidence(bundle, name))
        .collect()
}
fn read_evidence<T: serde::de::DeserializeOwned>(
    bundle: &RunBundle,
    name: &str,
) -> Result<T, BenchError> {
    let identity = bundle
        .files
        .get(name)
        .ok_or_else(|| BenchError::Evidence("unsealed evidence".into()))?;
    if std::path::Path::new(name)
        .components()
        .any(|part| !matches!(part, std::path::Component::Normal(_)))
    {
        return Err(BenchError::Evidence("unsafe evidence path".into()));
    }
    let path = bundle.path.join(name);
    crate::verify_file(&path, identity)?;
    Ok(serde_json::from_reader(std::fs::File::open(path)?)?)
}
/// Render stored observations with explicit writer ownership and flush propagation.
/// # Errors
/// Propagates invalid evidence and short/erroring writer or flush failures.
pub fn render(
    bundle: &RunBundle,
    format: ReportFormat,
    writer: &mut dyn Write,
) -> Result<(), BenchError> {
    render_record(&publication_record(bundle)?, format, writer)
}
/// Render a compact record after checking its structure and recomputing its analysis.
/// # Errors
/// Rejects stale/forged summaries, unsupported versions, size limits and writer failures.
pub fn render_record(
    record: &PublicationRecord,
    format: ReportFormat,
    writer: &mut dyn Write,
) -> Result<(), BenchError> {
    validate_record(record)?;
    if format == ReportFormat::Json {
        serde_json::to_writer_pretty(&mut *writer, record)?;
        writeln!(writer)?;
    } else {
        render_text(record, format, writer)?;
    }
    writer.flush()?;
    Ok(())
}
fn validate_record(record: &PublicationRecord) -> Result<(), BenchError> {
    if record.schema_version != 1
        || record.manifest.schema_version != 1
        || record.result.schema_version != 1
    {
        return Err(BenchError::Evidence(
            "unsupported publication schema".into(),
        ));
    }
    crate::suite::validate_identifier(&record.manifest.run_id)?;
    for artifact in record.manifest.roles.values() {
        crate::artifact::validate_identity(&artifact.file)?;
        if artifact.schema_version != 1
            || artifact.id
                != crate::artifact::json_identity(&(1_u32, &artifact.file, &artifact.build))?
        {
            return Err(BenchError::Evidence(
                "invalid artifact identity in publication".into(),
            ));
        }
        if let Some(build) = &artifact.build {
            build.validate()?;
        }
    }
    let mut expected = crate::analyze(
        &record.manifest,
        &record.analysis.timing_samples,
        &record.analysis.rss_samples,
    )?;
    if let Some(selection) = record.comparison {
        crate::analysis::select_comparison(&mut expected, &record.manifest, selection)?;
    }
    suppress_failed(&mut expected, record.result.outcome);
    if expected != record.analysis {
        return Err(BenchError::Evidence(
            "publication analysis differs from normalized observations".into(),
        ));
    }
    if serde_json::to_vec(record)?.len() > 16_777_216 {
        return Err(BenchError::Evidence("publication exceeds 16 MiB".into()));
    }
    Ok(())
}
fn render_text(
    record: &PublicationRecord,
    format: ReportFormat,
    writer: &mut dyn Write,
) -> Result<(), BenchError> {
    let markdown = format == ReportFormat::Markdown;
    let escape = |text: &str| escape_text(text, markdown);
    writeln!(
        writer,
        "{}CLI benchmark {}",
        if markdown { "# " } else { "" },
        escape(&record.manifest.run_id)
    )?;
    writeln!(
        writer,
        "Outcome: {:?}. Policy: descriptive-v1. Performance is advisory.",
        record.result.outcome
    )?;
    writeln!(
        writer,
        "Requested stages: {}.",
        match record.manifest.execution_kind {
            Some(crate::ExecutionKind::CheckOnly) => "check-only",
            Some(crate::ExecutionKind::Measure) => "measure",
            None => "unresolved",
        }
    )?;
    if let Some(message) = &record.result.message {
        writeln!(writer, "{}", escape(message))?;
    }
    writeln!(
        writer,
        "Contract: {}. Changes require a new experiment identity; compare contract IDs between attempts.",
        record
            .analysis
            .contract_id
            .as_deref()
            .unwrap_or("unavailable")
    )?;
    if record.analysis.smoke_only {
        writeln!(
            writer,
            "Smoke-only: no performance or confirmation conclusion."
        )?;
    }
    if let Some(experiment) = &record.manifest.experiment {
        writeln!(
            writer,
            "Experiment: {}. Hypothesis: {}. Change: {}.",
            escape(&experiment.id),
            escape(&experiment.hypothesis),
            escape(&experiment.change_summary)
        )?;
    }
    for (role, artifact) in &record.manifest.roles {
        writeln!(
            writer,
            "{role:?}: {} bytes; SHA-256 {}; source {}",
            artifact.file.bytes,
            escape(&artifact.file.sha256),
            escape(
                artifact
                    .build
                    .as_ref()
                    .map_or("unknown prebuilt provenance", |b| b.source_sha.as_str())
            )
        )?;
        if let Some(build) = &artifact.build {
            writeln!(
                writer,
                "Build: {}",
                escape(&serde_json::to_string(&build.policy)?)
            )?;
        }
    }
    for input in &record.manifest.inputs {
        writeln!(
            writer,
            "Input {}: {} bytes; SHA-256 {}",
            escape(&input.dataset),
            input.file.bytes,
            escape(&input.file.sha256)
        )?;
    }
    render_cases(record, markdown, writer)?;
    render_footer(record, markdown, writer)
}
fn render_footer(
    record: &PublicationRecord,
    markdown: bool,
    writer: &mut dyn Write,
) -> Result<(), BenchError> {
    let escape = |text: &str| escape_text(text, markdown);
    for issue in &record.analysis.issues {
        writeln!(writer, "{}", escape(issue))?;
    }
    writeln!(
        writer,
        "Correctness: {}",
        record
            .validation
            .as_ref()
            .map_or("unavailable", |v| if v.passed() {
                "passed"
            } else {
                "failed"
            })
    )?;
    writeln!(writer, "Replay recipe: {}", escape(&record.replay))?;
    writeln!(
        writer,
        "Artifact retention requested: {} days; expiry: {} (assigned by publisher).",
        record.requested_retention_days,
        record.expires_at_unix_seconds.map_or_else(
            || "unavailable for local evidence".into(),
            |v| v.to_string()
        )
    )?;
    if let Some(attempt) = &record.attempt {
        write_link(writer, "Attempt", attempt, markdown)?;
    }
    for evidence in &record.evidence {
        write_link(writer, evidence, evidence, markdown)?;
    }
    Ok(())
}
fn render_cases(
    record: &PublicationRecord,
    markdown: bool,
    writer: &mut dyn Write,
) -> Result<(), BenchError> {
    let escape = |text: &str| escape_text(text, markdown);
    for case in &record.analysis.cases {
        writeln!(
            writer,
            "\n{}{} — {} ({})",
            if markdown { "## " } else { "" },
            escape(&case.id),
            escape(&case.purpose),
            if case.confirmation {
                "confirmation workload"
            } else {
                "tuning workload"
            }
        )?;
        writeln!(
            writer,
            "I/O: {}. Work: {}.",
            escape(&serde_json::to_string(&case.io)?),
            escape(&serde_json::to_string(&case.work)?)
        )?;
        for (role, metrics) in &case.roles {
            writeln!(writer, "{role:?}: {} bytes", metrics.executable_bytes)?;
            for (batch, stats) in &metrics.batches {
                write_statistics(writer, &format!("{batch:?} seconds"), stats)?;
            }
            if let Some(stats) = &metrics.elapsed {
                write_statistics(writer, "Overall seconds", stats)?;
            }
            if let Some(stats) = &metrics.rss {
                write_statistics(
                    writer,
                    "RSS bytes (OS accounting; not allocations or summed process peaks)",
                    stats,
                )?;
            }
            if let Some(throughput) = metrics.throughput {
                writeln!(writer, "Throughput: {throughput:.6} declared units/second")?;
            }
            for issue in &metrics.issues {
                writeln!(writer, "{}", escape(issue))?;
            }
        }
        for comparison in &case.comparisons {
            writeln!(
                writer,
                "{:?}/{:?}: ratio {}; elapsed change {} seconds ({}%); {:?}; {}",
                comparison.candidate,
                comparison.baseline,
                optional(comparison.ratio),
                optional(comparison.elapsed_change_seconds),
                optional(comparison.elapsed_change_percent),
                comparison.direction,
                escape(&comparison.scope)
            )?;
            writeln!(
                writer,
                "Batch ratios: {}; size change {} bytes ({}%).",
                escape(&serde_json::to_string(&comparison.batch_ratios)?),
                comparison.size_change_bytes,
                optional(comparison.size_change_percent)
            )?;
            for limitation in &comparison.limitations {
                writeln!(writer, "{}", escape(limitation))?;
            }
        }
    }
    Ok(())
}
fn optional(value: Option<f64>) -> String {
    value.map_or_else(|| "unavailable".into(), |v| format!("{v:.6}"))
}
fn write_statistics(
    writer: &mut dyn Write,
    label: &str,
    stats: &crate::Statistics,
) -> std::io::Result<()> {
    writeln!(
        writer,
        "{label}: count {}; mean {:.6}; median {:.6}; sample stddev {}; range {:.6}–{:.6}",
        stats.count,
        stats.mean,
        stats.median,
        optional(stats.sample_stddev),
        stats.min,
        stats.max
    )
}
fn escape_text(text: &str, markdown: bool) -> String {
    let mut output = String::new();
    for ch in text.chars() {
        if ch.is_control() {
            output.extend(ch.escape_default());
        } else if markdown {
            match ch {
                '&' => output.push_str("&amp;"),
                '<' => output.push_str("&lt;"),
                '>' => output.push_str("&gt;"),
                '\\' | '`' | '*' | '_' | '{' | '}' | '[' | ']' | '(' | ')' | '#' | '+' | '-'
                | '.' | '!' | '|' => {
                    output.push('\\');
                    output.push(ch);
                }
                _ => output.push(ch),
            }
        } else {
            output.push(ch);
        }
    }
    output
}
fn write_link(
    writer: &mut dyn Write,
    label: &str,
    target: &str,
    markdown: bool,
) -> std::io::Result<()> {
    if markdown {
        let encoded: String = target
            .bytes()
            .map(|byte| {
                if byte.is_ascii_alphanumeric() || b"/-_.".contains(&byte) {
                    char::from(byte).to_string()
                } else {
                    format!("%{byte:02X}")
                }
            })
            .collect();
        writeln!(writer, "[{}]({encoded})", escape_text(label, true))
    } else {
        writeln!(
            writer,
            "{}: {}",
            escape_text(label, false),
            escape_text(target, false)
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn bundle() -> Result<(assert_fs::TempDir, RunBundle), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let store = crate::Store::open(&root.join("store"))?;
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let bundle = store
            .begin_run(&suite)?
            .record_failure(crate::RunOutcome::Failed, "bad <script>|[link]\n\x1b[31m")?;
        Ok((root, bundle))
    }
    #[test]
    fn failed_evidence_renders_all_formats_repeatedly_and_escapes_user_strings()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_root, bundle) = bundle()?;
        for format in [
            ReportFormat::Terminal,
            ReportFormat::Json,
            ReportFormat::Markdown,
        ] {
            let mut first = vec![];
            render(&bundle, format, &mut first)?;
            let mut second = vec![];
            render(&bundle, format, &mut second)?;
            crate::test_support::equal(&(first), &(second))?;
            crate::test_support::require(!first.contains(&27), "report assertion failed")?;
            let text = String::from_utf8(first)?;
            if format == ReportFormat::Markdown {
                crate::test_support::require(
                    !text.contains("<script>"),
                    "report assertion failed",
                )?;
                crate::test_support::require(!text.contains("[link]"), "report assertion failed")?;
            }
            if format == ReportFormat::Json {
                let record: PublicationRecord = serde_json::from_str(&text)?;
                crate::test_support::equal(&(record.result.outcome), &(crate::RunOutcome::Failed))?;
            }
        }
        crate::test_support::equal(
            &(publication_record(&bundle)?.expires_at_unix_seconds),
            &(None),
        )?;
        Ok(())
    }
    #[test]
    fn rendering_deserialized_records_rejects_forged_analysis_and_versions()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_root, bundle) = bundle()?;
        let record = publication_record(&bundle)?;
        for fault in ["analysis", "schema", "manifest", "result"] {
            let mut forged = record.clone();
            match fault {
                "analysis" => forged.analysis.issues.clear(),
                "schema" => forged.schema_version = 99,
                "manifest" => forged.manifest.schema_version = 99,
                _ => forged.result.schema_version = 99,
            }
            let mut output = vec![];
            crate::test_support::require(
                render_record(&forged, ReportFormat::Json, &mut output).is_err(),
                "forged record accepted",
            )?;
            crate::test_support::require(output.is_empty(), "invalid record partially written")?;
        }
        Ok(())
    }
    #[test]
    fn deserialized_record_rejects_unbound_artifact_identity()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_root, bundle) = bundle()?;
        let mut record = publication_record(&bundle)?;
        record.manifest.roles.insert(
            crate::Role::Candidate,
            crate::ArtifactRecord {
                schema_version: 1,
                id: "a".repeat(64),
                file: crate::FileIdentity {
                    sha256: "b".repeat(64),
                    bytes: 10,
                },
                build: None,
            },
        );
        crate::test_support::require(
            render_record(&record, ReportFormat::Json, &mut Vec::new()).is_err(),
            "unbound artifact identity rendered",
        )?;
        Ok(())
    }
    struct ShortWriter {
        bytes: Vec<u8>,
        fail_write: bool,
        fail_flush: bool,
    }
    impl Write for ShortWriter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if self.fail_write {
                return Err(std::io::Error::other("write failed"));
            }
            if let Some(byte) = bytes.first() {
                self.bytes.push(*byte);
                Ok(1)
            } else {
                Ok(0)
            }
        }
        fn flush(&mut self) -> std::io::Result<()> {
            if self.fail_flush {
                Err(std::io::Error::other("flush failed"))
            } else {
                Ok(())
            }
        }
    }
    #[test]
    fn short_writes_are_completed_and_write_flush_errors_propagate()
    -> Result<(), Box<dyn std::error::Error>> {
        let (_root, bundle) = bundle()?;
        for format in [
            ReportFormat::Terminal,
            ReportFormat::Json,
            ReportFormat::Markdown,
        ] {
            let mut expected = vec![];
            render(&bundle, format, &mut expected)?;
            for (fail_write, fail_flush) in [(false, false), (true, false), (false, true)] {
                let mut writer = ShortWriter {
                    bytes: vec![],
                    fail_write,
                    fail_flush,
                };
                let result = render(&bundle, format, &mut writer);
                if fail_write || fail_flush {
                    crate::test_support::require(result.is_err(), "report assertion failed")?;
                } else {
                    result?;
                    crate::test_support::equal(&(writer.bytes), &(expected))?;
                }
            }
        }
        Ok(())
    }
    #[test]
    fn publication_fixture_matches_markdown_golden_and_rejects_unknown_fields()
    -> Result<(), Box<dyn std::error::Error>> {
        let text = include_str!("../tests/inputs/publication-failed.json");
        let record: PublicationRecord = serde_json::from_str(text)?;
        let mut bytes = vec![];
        render_record(&record, ReportFormat::Markdown, &mut bytes)?;
        crate::test_support::equal(
            &String::from_utf8(bytes)?,
            &include_str!("../tests/expected/failed-report.md"),
        )?;
        let mut value: serde_json::Value = serde_json::from_str(text)?;
        value["unexpected"] = serde_json::json!(true);
        crate::test_support::require(
            serde_json::from_value::<PublicationRecord>(value).is_err(),
            "publication accepted unknown field",
        )?;
        Ok(())
    }
}
