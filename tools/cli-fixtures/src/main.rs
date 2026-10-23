use anyhow::{Context as _, Result, bail};
use clap::{Parser, Subcommand};
use cli_testing::{
    catalog::{Case, Execution, Input, Suite, Transform},
    recording, reference,
};
use std::path::PathBuf;

/// Inspect CLI test recipes and stage independent reference fixtures.
#[derive(Parser)]
#[command(version)]
struct Args {
    /// Owning package's tests/cases.toml.
    #[arg(long)]
    suite: PathBuf,
    #[command(subcommand)]
    command: Action,
}

#[derive(Subcommand)]
enum Action {
    /// Show case names, ownership, invocations and expectation origins.
    List,
    /// Show the full recipe and declared differences for one case.
    Explain { name: String },
    /// Validate the catalog and reject unreferenced expected files.
    Validate,
    /// Check automatic cases against their independent references.
    Compare {
        /// Built candidate executable (no build or PATH lookup is performed).
        #[arg(long)]
        candidate: PathBuf,
        /// Limit comparison to one named case.
        #[arg(long)]
        case: Option<String>,
    },
    /// Stage raw reference output and provenance; never rewrite fixtures.
    Record {
        /// New directory outside the package's tests directory.
        #[arg(long)]
        output: PathBuf,
    },
    /// Promote a completed recording after reviewing its raw bytes and provenance.
    Apply { directory: PathBuf },
}

fn main() -> Result<()> {
    let args = Args::parse();
    let suite =
        Suite::load(&args.suite).with_context(|| format!("loading {}", args.suite.display()))?;
    match args.command {
        Action::List => {
            for case in &suite.cases {
                describe(&suite, case);
            }
        }
        Action::Explain { name } => describe(&suite, suite.case(&name)?),
        Action::Validate => {
            let unused = suite.unreferenced_expected()?;
            if !unused.is_empty() {
                bail!("unreferenced expected files: {unused:?}");
            }
            println!(
                "{}: {} cases valid; every expected file has a catalog owner",
                suite.binary,
                suite.cases.len()
            );
        }
        Action::Compare { candidate, case } => compare(&suite, &candidate, case.as_deref())?,
        Action::Record { output } => {
            let count = recording::record(&suite, &output)?;
            println!(
                "Recorded {count} independent recipes in {}. Review recording.json and raw streams before apply.",
                output.display()
            );
        }
        Action::Apply { directory } => {
            let changed = recording::apply(&suite, &directory)?;
            for path in &changed {
                println!("updated {}", path.display());
            }
            println!("Applied recording: {} fixture files changed", changed.len());
        }
    }
    Ok(())
}

fn describe(suite: &Suite, case: &Case) {
    let owner = match case.execution {
        Execution::Toml => "toml",
        Execution::Rust => "rust",
    };
    println!("{} [{owner}]", case.name);
    println!(
        "  candidate: {} {:?}; cwd: {}",
        suite.binary,
        case.args,
        suite.package_dir().display()
    );
    println!("  input dependencies: {:?}", case.inputs);
    match &case.stdin {
        None => println!("  stdin: closed"),
        Some(Input::Pipe {
            text: Some(text), ..
        }) => println!("  stdin: pipe, text {text:?}"),
        Some(Input::Pipe { files, .. }) => println!("  stdin: pipe, concatenate {files:?}"),
        Some(Input::File { path }) => println!("  stdin: regular file {}", path.display()),
    }
    println!(
        "  environment overrides: suite {:?}, case {:?}",
        suite.env, case.env
    );
    println!(
        "  expect: exit {}; stdout {}; stderr {}",
        case.expect.exit,
        stream_description(
            case.expect.stdout.as_deref(),
            case.expect.stdout_file.as_deref()
        ),
        stream_description(
            case.expect.stderr.as_deref(),
            case.expect.stderr_file.as_deref()
        )
    );
    if let Some(name) = &case.origin.reference
        && let Some(reference) = suite.references.get(name)
    {
        println!(
            "  reference {name}: {} {:?} ({})",
            reference.program.display(),
            case.origin.args.as_ref().unwrap_or(&case.args),
            reference.platform
        );
        println!(
            "  reference status: {}; environment overrides: {:?}, origin {:?}",
            case.origin.exit.unwrap_or(case.expect.exit),
            reference.env,
            case.origin.env
        );
        if let Some(stderr) = &case.origin.stderr {
            println!("  reference stderr: text {stderr:?}");
        }
    }
    if let Some(reason) = &case.origin.manual {
        println!("  manual expectation: {reason}");
    }
    if let Some(reason) = &case.origin.reason {
        println!("  reference difference: {reason}");
    }
    if let Some(adapt) = &case.adapt {
        let name = match adapt.expected_stdout {
            Transform::Utf8Lossy => "utf8-lossy",
        };
        println!("  expected stdout adaptation: {name}; {}", adapt.reason);
    }
}

fn stream_description(text: Option<&str>, file: Option<&std::path::Path>) -> String {
    file.map_or_else(
        || format!("text {:?}", text.unwrap_or_default()),
        |file| format!("file {}", file.display()),
    )
}

fn compare(suite: &Suite, candidate: &std::path::Path, name: Option<&str>) -> Result<()> {
    let candidate = candidate
        .canonicalize()
        .context("candidate executable is unavailable")?;
    let cases = if let Some(name) = name {
        vec![suite.case(name)?]
    } else {
        suite.cases.iter().collect()
    };
    let mut failures = Vec::new();
    for case in cases {
        if case.execution == Execution::Rust {
            println!(
                "{}: requires native Rust tests (execution = rust)",
                case.name
            );
            continue;
        }
        match reference::compare(suite, case, &candidate) {
            Ok(()) => println!(
                "{}: {}",
                case.name,
                if case.origin.reference.is_some() {
                    "reference and candidate agree"
                } else {
                    "candidate matches manual expectation; no reference"
                }
            ),
            Err(error) => failures.push(format!("{}: {:#}", case.name, anyhow::Error::new(error))),
        }
    }
    if !failures.is_empty() {
        bail!("comparison failed:\n{}", failures.join("\n"));
    }
    Ok(())
}
