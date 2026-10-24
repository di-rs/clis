mod describe;
mod discovery;

use anyhow::{Context as _, Result, bail};
use clap::{Args as ClapArgs, Parser, Subcommand};
use cli_testing::{
    catalog::{Execution, Suite},
    recording, reference,
};
use serde_json::{Value, json};
use std::{
    fmt::Write as _,
    path::{Path, PathBuf},
};

/// Inspect CLI test cases, compare real CLIs, and stage reference fixtures.
#[derive(Parser)]
#[command(version)]
pub struct Args {
    /// Emit a schema-versioned JSON envelope, including failures.
    #[arg(long, global = true)]
    pub json: bool,
    /// Owning package's tests/cases.toml; required for catalog operations.
    #[arg(long, global = true)]
    pub suite: Option<PathBuf>,
    #[command(subcommand)]
    pub command: Action,
}

#[derive(ClapArgs)]
pub struct Page {
    /// Maximum entries to return (1–1000).
    #[arg(long, default_value_t = 50, value_parser = clap::value_parser!(u16).range(1..=1000))]
    limit: u16,
    /// Number of entries to skip in the stable ordering.
    #[arg(long, default_value_t = 0)]
    offset: usize,
}

impl Page {
    fn select<'a, T>(&self, values: &'a [T]) -> (&'a [T], Option<usize>) {
        let start = self.offset.min(values.len());
        let end = start
            .saturating_add(usize::from(self.limit))
            .min(values.len());
        (
            values.get(start..end).unwrap_or_default(),
            (end < values.len()).then_some(end),
        )
    }
}

#[derive(Subcommand)]
pub enum Action {
    /// Check offline setup, fixture availability, and reference prerequisites.
    Doctor,
    /// Find package catalogs below a directory; skips hidden/build directories and symlinks.
    Discover {
        #[arg(long, default_value = ".")]
        root: PathBuf,
        #[command(flatten)]
        page: Page,
    },
    /// List case recipes in catalog order.
    List {
        #[command(flatten)]
        page: Page,
    },
    /// Resolve an exact case name to its stable catalog ID and execution owner.
    Resolve { name: String },
    /// Show one full recipe and its declared reference differences.
    Explain { name: String },
    /// Export the complete validated catalog (TOML in text mode, model in JSON).
    Catalog,
    /// Validate recipes and reject unreferenced expected files.
    Validate,
    /// Check automatic cases against committed expectations and independent references.
    Compare {
        /// Built candidate executable; no build or PATH lookup is performed.
        #[arg(long)]
        candidate: PathBuf,
        #[arg(long)]
        case: Option<String>,
    },
    /// Stage raw independent reference outputs and provenance for review.
    Record {
        /// New directory outside the package's tests directory.
        #[arg(long)]
        output: PathBuf,
    },
    /// Promote a reviewed recording to its declared expected fixtures.
    Apply {
        directory: PathBuf,
        /// Run all preflight checks and list changes without writing fixtures.
        #[arg(long)]
        dry_run: bool,
    },
}

impl Action {
    pub const fn name(&self) -> &'static str {
        match self {
            Self::Doctor => "doctor",
            Self::Discover { .. } => "discover",
            Self::List { .. } => "list",
            Self::Resolve { .. } => "resolve",
            Self::Explain { .. } => "explain",
            Self::Catalog => "catalog",
            Self::Validate => "validate",
            Self::Compare { .. } => "compare",
            Self::Record { .. } => "record",
            Self::Apply { .. } => "apply",
        }
    }
}

pub struct Report {
    pub data: Value,
    pub text: String,
    pub error: Option<Value>,
}

impl Report {
    pub const fn new(data: Value, text: String) -> Self {
        Self {
            data,
            text,
            error: None,
        }
    }

    pub fn fail(&mut self, code: &str, message: &str) {
        self.error = Some(json!({"code": code, "message": message}));
    }
}

pub fn execute(args: &Args) -> Result<Report> {
    match &args.command {
        Action::Doctor => doctor(args.suite.as_deref()),
        Action::Discover { root, page } => discovery::discover(root, page),
        action => {
            let path = args
                .suite
                .as_ref()
                .context("--suite PATH is required for this command")?;
            let suite = Suite::load(path).with_context(|| format!("loading {}", path.display()))?;
            execute_catalog(&suite, action)
        }
    }
}

fn execute_catalog(suite: &Suite, action: &Action) -> Result<Report> {
    let path = suite.path();
    let report = match action {
        Action::List { page } => {
            let (cases, next_offset) = page.select(&suite.cases);
            let text = cases
                .iter()
                .map(|case| describe::describe(suite, case))
                .collect();
            Report::new(
                json!({"suite": path, "cases": cases, "total": suite.cases.len(), "next_offset": next_offset}),
                text,
            )
        }
        Action::Resolve { name } => {
            let case = suite.case(name)?;
            Report::new(
                json!({"suite": path, "id": case.name, "execution": case.execution}),
                format!("{}: {}\n", path.display(), case.name),
            )
        }
        Action::Explain { name } => {
            let case = suite.case(name)?;
            Report::new(
                json!({"suite": path, "binary": suite.binary, "cwd": suite.package_dir(), "env": suite.env, "references": suite.references, "case": case}),
                describe::describe(suite, case),
            )
        }
        Action::Catalog => Report::new(
            json!({"suite": path, "catalog": suite}),
            std::fs::read_to_string(&path)?,
        ),
        Action::Validate => {
            let unused = suite.unreferenced_expected()?;
            if !unused.is_empty() {
                bail!("unreferenced expected files: {unused:?}");
            }
            Report::new(
                json!({"suite": path, "cases": suite.cases.len(), "unreferenced_expected": unused}),
                format!(
                    "{}: {} cases valid; every expected file has a catalog owner\n",
                    suite.binary,
                    suite.cases.len()
                ),
            )
        }
        Action::Compare { candidate, case } => return compare(suite, candidate, case.as_deref()),
        Action::Record { output } => {
            let count = recording::record(suite, output)?;
            let directory = output.canonicalize()?;
            Report::new(
                json!({"suite": path, "directory": directory, "manifest": directory.join("recording.json"), "cases": count}),
                format!(
                    "Recorded {count} independent recipes in {}. Review recording.json and raw streams before apply.\n",
                    directory.display()
                ),
            )
        }
        Action::Apply { directory, dry_run } => {
            let changed = if *dry_run {
                recording::preview(suite, directory)?
            } else {
                recording::apply(suite, directory)?
            };
            let text = if *dry_run {
                format!(
                    "Preview: {} fixture files would change: {changed:?}\n",
                    changed.len()
                )
            } else {
                format!(
                    "Applied recording: {} fixture files changed: {changed:?}\n",
                    changed.len()
                )
            };
            Report::new(
                json!({"suite": path, "directory": directory.canonicalize()?, "dry_run": dry_run, "changed_files": changed}),
                text,
            )
        }
        Action::Doctor | Action::Discover { .. } => {
            bail!("this command does not operate on a catalog")
        }
    };
    Ok(report)
}

fn doctor(path: Option<&Path>) -> Result<Report> {
    let suite = path.map(Suite::load).transpose()?;
    let references: Vec<_> = suite.iter().flat_map(|suite| &suite.references).map(|(name, reference)| {
        json!({"name": name, "program": reference.program, "platform": reference.platform, "platform_matches": reference.platform == std::env::consts::OS, "executable_found": reference.program.is_file()})
    }).collect();
    Ok(Report::new(
        json!({
            "version": env!("CARGO_PKG_VERSION"), "mode": "offline", "auth": "not_required",
            "suite": suite.as_ref().map(Suite::path), "fixtures_found": suite.is_some(),
            "references": references,
            "next_step": if suite.is_some() { "validate the catalog before comparison or recording" } else { "discover --root DIR, then pass --suite PATH" },
        }),
        format!(
            "cli-testing {}: offline; authentication not required\n{}\n",
            env!("CARGO_PKG_VERSION"),
            if suite.is_some() {
                "Catalog and fixtures loaded; reference prerequisites are available in --json output."
            } else {
                "No suite selected. Use discover --root DIR, then --suite PATH."
            }
        ),
    ))
}

fn compare(suite: &Suite, candidate: &Path, name: Option<&str>) -> Result<Report> {
    let candidate = candidate
        .canonicalize()
        .context("candidate executable is unavailable")?;
    let cases = if let Some(name) = name {
        vec![suite.case(name)?]
    } else {
        suite.cases.iter().collect()
    };
    let mut results = Vec::new();
    let mut text = String::new();
    let mut failed = false;
    for case in cases {
        let (status, message) = if case.execution == Execution::Rust {
            (
                "native_required",
                "requires native Rust tests (execution = rust)".to_owned(),
            )
        } else {
            match reference::compare(suite, case, &candidate) {
                Ok(()) if case.origin.reference.is_some() => {
                    ("matched", "reference and candidate agree".to_owned())
                }
                Ok(()) => (
                    "manual_matched",
                    "candidate matches manual expectation; no reference".to_owned(),
                ),
                Err(error) => {
                    failed = true;
                    ("failed", format!("{:#}", anyhow::Error::new(error)))
                }
            }
        };
        results.push(json!({"id": case.name, "status": status}));
        let _ = writeln!(text, "{}: {message}", case.name);
    }
    let mut report = Report::new(
        json!({"suite": suite.path(), "candidate": candidate, "cases": results}),
        text,
    );
    if failed {
        report.fail(
            "comparison_failed",
            "one or more cases failed; rerun without --json for diagnostics",
        );
    }
    Ok(report)
}
