//! Offline consumer: explicit store/run selection, no logger or measurement lock.
use cli_bench::{ReportFormat, Store, analyze, publication_record, render};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let store = Store::open_existing(&std::path::PathBuf::from(
        arguments.next().ok_or("store path required")?,
    ))?;
    let id = arguments
        .next()
        .ok_or("run ID required")?
        .into_string()
        .map_err(|_| "run ID must be UTF-8")?;
    let bundle = store.load_run(&id)?;
    for _ in 0..2 {
        let record = publication_record(&bundle)?;
        let regenerated = analyze(
            &record.manifest,
            &record.analysis.timing_samples,
            &record.analysis.rss_samples,
        )?;
        if regenerated.policy != "descriptive-v1" {
            return Err("unexpected policy".into());
        }
        render(
            &bundle,
            ReportFormat::Terminal,
            &mut std::io::stdout().lock(),
        )?;
    }
    Ok(())
}
