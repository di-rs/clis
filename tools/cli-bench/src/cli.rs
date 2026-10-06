use clap::{Args, CommandFactory, Parser, Subcommand};
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(version, about, long_about = env!("CARGO_PKG_DESCRIPTION"))]
pub struct Cli {
    #[command(flatten)]
    pub logging: cli_tracing::GlobalLogArgs,
    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Build a committed Cargo package and retain its verified executable.
    Build(BuildArgs),
    /// Validate selection arguments; execution is not yet implemented.
    Run(SelectionArgs),
    /// Validate selection arguments; execution is not yet implemented.
    Check(SelectionArgs),
}

#[derive(Debug, Args)]
pub struct BuildArgs {
    #[arg(short, long)]
    pub package: String,
    #[arg(short, long)]
    pub revision: String,
    #[arg(short, long)]
    pub toolchain: Option<String>,
    #[arg(short = 'F', long, value_delimiter = ',')]
    pub features: Vec<String>,
    #[arg(short = 'N', long)]
    pub no_default_features: bool,
    #[arg(short, long, default_value = ".cli-bench")]
    pub data_dir: PathBuf,
}

#[derive(Debug, Args)]
#[command(group(clap::ArgGroup::new("candidate-binding").required(true).args(["candidate_ref", "candidate"])),
    group(clap::ArgGroup::new("comparator-binding").required(true).multiple(true).args(["previous_ref", "previous", "reference"])))]
pub struct SelectionArgs {
    #[arg(short, long)]
    pub suite: String,
    #[arg(short = 'r', long, conflicts_with = "candidate")]
    pub candidate_ref: Option<String>,
    #[arg(short = 'a', long)]
    pub candidate: Option<PathBuf>,
    #[arg(short = 'b', long, conflicts_with = "previous")]
    pub previous_ref: Option<String>,
    #[arg(short = 'p', long)]
    pub previous: Option<PathBuf>,
    #[arg(short = 'x', long)]
    pub reference: Option<PathBuf>,
    #[arg(short = 'G', long, conflicts_with = "biggie")]
    pub generator_ref: Option<String>,
    #[arg(short = 'g', long)]
    pub biggie: Option<PathBuf>,
}

impl Cli {
    pub fn try_parse_validated_from(
        args: impl IntoIterator<Item = impl Into<std::ffi::OsString> + Clone>,
    ) -> Result<(Self, cli_tracing::LogArgs), clap::Error> {
        let cli = Self::try_parse_from(args)?;
        let logging = cli.logging.resolve().map_err(|error| {
            Self::command().error(clap::error::ErrorKind::ValueValidation, error)
        })?;
        Ok((cli, logging))
    }
}

pub fn execute_build(args: &BuildArgs) -> anyhow::Result<std::process::ExitCode> {
    use cli_bench::{
        BuildPolicy, BuildRequest, BuildTools, ExecutionPolicy, GitContext, ProcessRunner, Store,
    };
    use std::{
        io::Write,
        sync::{Arc, atomic::AtomicBool},
        time::Duration,
    };
    let cwd = std::env::current_dir()?;
    let scratch = AdapterScratch::new()?;
    let environment = build_environment();
    let runner = ProcessRunner::new(ExecutionPolicy {
        timeout: Duration::from_mins(30),
        max_stream_bytes: 268_435_456,
        cancellation: Arc::new(AtomicBool::new(false)),
    });
    let context = AdapterTools {
        cwd: &cwd,
        environment: &environment,
        runner: &runner,
    };
    let git = GitContext {
        tool: context.identify(&context.find("git")?, &["--version".into()])?,
        environment: environment.clone(),
        scratch_root: scratch.0.clone(),
    };
    let revision = cli_bench::resolve_revision(&cwd, &args.revision, &git, &runner)?;
    if args.revision == "HEAD" && revision.dirty {
        let mut diagnostic = std::io::stderr().lock();
        writeln!(
            diagnostic,
            "HEAD uses committed source; uncommitted edits are excluded. Use prebuilt mode to compare local edits."
        )?;
        diagnostic.flush()?;
    }
    let rustup = context.find("rustup")?;
    let toolchain = match &args.toolchain {
        Some(value) => value.clone(),
        None => context
            .run(&rustup, &["show".into(), "active-toolchain".into()])?
            .split_whitespace()
            .next()
            .ok_or_else(|| anyhow::anyhow!("no active installed toolchain"))?
            .into(),
    };
    let locate = |name: &str| -> anyhow::Result<PathBuf> {
        let path = context.run(
            &rustup,
            &[
                "which".into(),
                "--toolchain".into(),
                toolchain.clone(),
                name.into(),
            ],
        )?;
        Ok(std::fs::canonicalize(path.trim())?)
    };
    let tools = BuildTools {
        cargo: context.identify(&locate("cargo")?, &["-Vv".into()])?,
        rustc: context.identify(&locate("rustc")?, &["-Vv".into()])?,
        environment: environment.clone(),
    };
    let store = Store::open(&args.data_dir)?;
    let artifact = cli_bench::build_revision(
        &BuildRequest {
            repository: cwd.clone(),
            revision,
            package: args.package.clone(),
            binary: None,
            policy: BuildPolicy {
                toolchain: Some(toolchain),
                features: args.features.clone(),
                no_default_features: args.no_default_features,
                ..BuildPolicy::default()
            },
            git,
            tools,
            cache_root: cwd.join("target/cli-bench"),
        },
        &store,
        &runner,
    )?;
    let mut output = std::io::stdout().lock();
    serde_json::to_writer_pretty(&mut output, &artifact)?;
    writeln!(output)?;
    output.flush()?;
    Ok(std::process::ExitCode::SUCCESS)
}

fn build_environment() -> std::collections::BTreeMap<String, String> {
    let environment: std::collections::BTreeMap<String, String> = [
        "PATH",
        "HOME",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "SDKROOT",
        "DEVELOPER_DIR",
        "TMPDIR",
    ]
    .into_iter()
    .filter_map(|name| std::env::var(name).ok().map(|value| (name.into(), value)))
    .chain([
        ("LC_ALL".into(), "C".into()),
        ("RUSTUP_AUTO_INSTALL".into(), "0".into()),
    ])
    .collect();
    environment
}

struct AdapterScratch(PathBuf);
impl AdapterScratch {
    fn new() -> anyhow::Result<Self> {
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("cli-bench-{timestamp}-{}", std::process::id()));
        std::fs::create_dir(&path)?;
        Ok(Self(std::fs::canonicalize(path)?))
    }
}
impl Drop for AdapterScratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
struct AdapterTools<'a> {
    cwd: &'a std::path::Path,
    environment: &'a std::collections::BTreeMap<String, String>,
    runner: &'a cli_bench::ProcessRunner,
}
impl AdapterTools<'_> {
    fn find(&self, name: &str) -> anyhow::Result<PathBuf> {
        let path = self
            .environment
            .get("PATH")
            .ok_or_else(|| anyhow::anyhow!("PATH unavailable for requested tool {name}"))?;
        for directory in std::env::split_paths(path) {
            let candidate = self.cwd.join(directory).join(name);
            if candidate.is_file() {
                return Ok(std::fs::canonicalize(candidate)?);
            }
        }
        anyhow::bail!("requested tool {name} is unavailable; install it explicitly before building")
    }
    fn run(&self, program: &std::path::Path, args: &[String]) -> anyhow::Result<String> {
        let capture = AdapterScratch::new()?;
        let paths = cli_bench::CapturePaths {
            stdout: capture.0.join("stdout"),
            stderr: capture.0.join("stderr"),
        };
        self.runner
            .execute(
                &cli_bench::CommandSpec {
                    program: program.into(),
                    argv: args.into(),
                    cwd: self.cwd.into(),
                    environment: self.environment.clone(),
                    stdin: cli_bench::CommandInput::Null,
                    stdout: cli_bench::CommandOutput::Capture,
                },
                &paths,
            )?
            .check_expected(0)?;
        Ok(std::fs::read_to_string(paths.stdout)?)
    }
    fn identify(
        &self,
        program: &std::path::Path,
        args: &[String],
    ) -> anyhow::Result<cli_bench::BoundTool> {
        let file = cli_bench::fingerprint(program)?;
        let version = self.run(program, args)?.trim().into();
        cli_bench::verify_file(program, &file)?;
        Ok(cli_bench::BoundTool {
            path: program.into(),
            identity: cli_bench::ToolIdentity { file, version },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::Cli;

    #[test]
    fn run_and_check_reject_conflicting_role_bindings() {
        for command in ["run", "check"] {
            for conflicting in [
                vec!["-r", "HEAD", "-a", "/candidate", "-x", "/reference"],
                vec!["-a", "/candidate", "-b", "HEAD~1", "-p", "/previous"],
                vec![
                    "-a",
                    "/candidate",
                    "-p",
                    "/previous",
                    "-G",
                    "HEAD",
                    "-g",
                    "/biggie",
                ],
            ] {
                let args = ["cli-bench", command, "-s", "tailr"]
                    .into_iter()
                    .chain(conflicting);
                assert_eq!(
                    Cli::try_parse_validated_from(args)
                        .err()
                        .map(|error| error.kind()),
                    Some(clap::error::ErrorKind::ArgumentConflict),
                );
            }
        }
    }

    #[test]
    fn run_and_check_require_candidate_and_comparator() {
        for command in ["run", "check"] {
            for selection in [vec!["-a", "/candidate"], vec!["-x", "/reference"]] {
                assert_eq!(
                    Cli::try_parse_validated_from(
                        ["cli-bench", command, "-s", "tailr"]
                            .into_iter()
                            .chain(selection)
                    )
                    .err()
                    .map(|error| error.kind()),
                    Some(clap::error::ErrorKind::MissingRequiredArgument),
                );
            }
        }
    }

    #[test]
    fn rejects_invalid_effective_logging_before_initialization() {
        let error = Cli::try_parse_validated_from(["cli-bench", "--log-level", "invalid"])
            .err()
            .map(|error| error.kind());
        assert_eq!(error, Some(clap::error::ErrorKind::ValueValidation));
    }

    #[test]
    fn help_and_version_bypass_logging_validation() {
        for (flag, expected) in [
            ("--help", clap::error::ErrorKind::DisplayHelp),
            ("--version", clap::error::ErrorKind::DisplayVersion),
        ] {
            assert_eq!(
                Cli::try_parse_validated_from(["cli-bench", flag, "-L", "invalid"])
                    .err()
                    .map(|error| error.kind()),
                Some(expected)
            );
        }
    }
}
