use super::bench_api as cli_bench;
use super::validation_support::{Fixture, TestResult};

pub fn fake_timer(
    _validated: cli_bench::ValidatedExperiment<'_>,
    marker: &std::path::Path,
) -> std::io::Result<()> {
    std::fs::write(marker, b"timer invoked")
}

pub fn failed_gate_never_times(fixture: &Fixture) -> TestResult {
    let marker = fixture.root.path().join("timed");
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let result = fixture
        .prepare(cli_bench::MeasurementProfile::Full)
        .and_then(|prepared| {
            cli_bench::validate_experiment(prepared, &mut writer, &fixture.runner)
        });
    let failed = result.is_err();
    if let Ok(validated) = result {
        fake_timer(validated, &marker)?;
    }
    if !failed {
        return Err("failed correctness gate yielded a validation capability".into());
    }
    if marker.exists() {
        return Err("timer was invoked after gate failure".into());
    }
    Ok(())
}
