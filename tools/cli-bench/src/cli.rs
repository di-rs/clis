mod discovery;

use clap::{Args, CommandFactory, Parser, Subcommand};
use cli_bench::{
    BoundTool, CaseId, ExecutableSource, ExecutionPolicy, ExperimentPreparation, ExperimentRequest,
    GitContext, MeasurementProfile, PipelineTools, Platform, ProcessRunner, RoleRequest, RunMode,
    RunOutcome, RunRequest, StdinPolicy, Store, Suite, ToolIdentity, collect_host, fingerprint,
    parse_suite, render, run, validate_suite,
};
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
    /// Run correctness-checked elapsed, memory and size measurements.
    Run(SelectionArgs),
    /// Check selected workloads without timing or memory measurements.
    Check(SelectionArgs),
    /// Render a sealed local run without executing workloads.
    Report(ReportArgs),
    /// Compare two original roles in one sealed local run.
    Compare(CompareArgs),
    /// Export verified evidence with optional resource bytes.
    Export(ExportArgs),
    /// Strictly replay exact saved resources into a new run.
    Replay(ReplayArgs),
    /// List compact history or sealed local runs without executing tools.
    History(HistoryArgs),
}

#[derive(Debug, Args)]
pub struct ReportArgs {
    #[arg(short, long)]
    pub input: PathBuf,
    #[arg(short = 'f', long, default_value = "terminal", value_parser = ["terminal", "json", "markdown"])]
    pub format: String,
}

#[derive(Debug, Args)]
pub struct CompareArgs {
    #[command(flatten)]
    pub report: ReportArgs,
    #[arg(short = 'b', long, value_parser = parse_role)]
    pub baseline: cli_bench::Role,
    #[arg(short = 'a', long, value_parser = parse_role)]
    pub candidate: cli_bench::Role,
}
fn parse_role(value: &str) -> Result<cli_bench::Role, String> {
    match value {
        "candidate" => Ok(cli_bench::Role::Candidate),
        "previous" => Ok(cli_bench::Role::Previous),
        "reference" => Ok(cli_bench::Role::Reference),
        _ => Err("role must be candidate, previous or reference".into()),
    }
}

pub fn execute_offline(
    args: &ReportArgs,
    selection: Option<cli_bench::ComparisonSelection>,
) -> anyhow::Result<std::process::ExitCode> {
    let bundle = cli_bench::load_bundle(&args.input)?;
    let mut output = std::io::stdout().lock();
    let format = report_format(&args.format);
    if let Some(selection) = selection {
        let record = cli_bench::comparison_record(&bundle, selection)?;
        cli_bench::render_record(&record, format, &mut output)?;
    } else {
        render(&bundle, format, &mut output)?;
    }
    Ok(std::process::ExitCode::SUCCESS)
}

#[derive(Debug, Args)]
pub struct ExportArgs {
    #[arg(short, long)]
    pub input: PathBuf,
    #[arg(short, long)]
    pub output: PathBuf,
    #[arg(short = 'I', long)]
    pub with_inputs: bool,
    #[arg(short = 'B', long)]
    pub with_binaries: bool,
}
#[derive(Debug, Args)]
pub struct HistoryArgs {
    #[arg(short, long, default_value = ".cli-bench/runs")]
    pub directory: PathBuf,
    #[arg(short, long)]
    pub suite: Option<String>,
    #[arg(short = 'f', long, default_value = "terminal", value_parser = ["terminal", "json", "markdown"])]
    pub format: String,
}
#[derive(Debug, Args)]
pub struct ReplayArgs {
    #[arg(short, long)]
    pub input: PathBuf,
    #[arg(short = 'a', long)]
    pub candidate: Option<PathBuf>,
    #[arg(short = 'P', long)]
    pub previous: Option<PathBuf>,
    #[arg(short = 'x', long)]
    pub reference: Option<PathBuf>,
    #[arg(short = 'g', long)]
    pub biggie: Option<PathBuf>,
    #[arg(short = 'H', long)]
    pub hyperfine: Option<PathBuf>,
    #[arg(short = 'd', long, default_value = ".cli-bench")]
    pub data_dir: PathBuf,
    #[arg(short = 'f', long, default_value = "terminal", value_parser = ["terminal", "json", "markdown"])]
    pub format: String,
}
pub fn execute_export(args: &ExportArgs) -> anyhow::Result<std::process::ExitCode> {
    use std::io::Write;
    let bundle = cli_bench::load_bundle(&args.input)?;
    let index = cli_bench::export_bundle(
        &bundle,
        &cli_bench::ExportRequest {
            destination: args.output.clone(),
            with_inputs: args.with_inputs,
            with_binaries: args.with_binaries,
        },
    )?;
    let mut output = std::io::stdout().lock();
    serde_json::to_writer_pretty(&mut output, &index)?;
    writeln!(output)?;
    output.flush()?;
    Ok(std::process::ExitCode::SUCCESS)
}
pub fn execute_history(args: &HistoryArgs) -> anyhow::Result<std::process::ExitCode> {
    use std::io::Write;
    let records = cli_bench::list_history(
        &args.directory,
        &cli_bench::HistoryFilter {
            suite: args.suite.clone(),
        },
    )?;
    let mut output = std::io::stdout().lock();
    if args.format == "json" {
        serde_json::to_writer_pretty(&mut output, &records)?;
        writeln!(output)?;
    } else {
        for record in records {
            cli_bench::render_record(
                &record.publication,
                report_format(&args.format),
                &mut output,
            )?;
            for omission in &record.omissions {
                writeln!(output, "{omission}")?;
            }
        }
    }
    output.flush()?;
    Ok(std::process::ExitCode::SUCCESS)
}
pub fn execute_replay(args: &ReplayArgs) -> anyhow::Result<std::process::ExitCode> {
    with_signals(|cancellation| replay_inner(args, cancellation))
}
fn replay_inner(
    args: &ReplayArgs,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<std::process::ExitCode> {
    let bundle = cli_bench::load_bundle(&args.input)?;
    let contract = bundle.manifest.contract.as_ref().ok_or_else(|| {
        anyhow::anyhow!("strict replay requires a resolved contract; use run for a new experiment")
    })?;
    let lock = acquire_measurement_lock(cancellation)?;
    let scratch = AdapterScratch::new()?;
    let home = scratch.0.join("home");
    let config = scratch.0.join("config");
    std::fs::create_dir(&home)?;
    std::fs::create_dir(&config)?;
    let mut bindings = cli_bench::replay_bindings(&bundle, &home, &config)?;
    for (role, path) in [
        (cli_bench::Role::Candidate, &args.candidate),
        (cli_bench::Role::Previous, &args.previous),
        (cli_bench::Role::Reference, &args.reference),
    ] {
        if let Some(path) = path {
            bindings
                .roles
                .get_mut(&role)
                .ok_or_else(|| anyhow::anyhow!("strict replay cannot add an unselected role"))?
                .path = std::fs::canonicalize(path)?;
        }
    }
    if let Some(path) = &args.biggie {
        bindings
            .generator
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("missing Biggie identity"))?
            .path = std::fs::canonicalize(path)?;
    }
    let runner = ProcessRunner::new(ExecutionPolicy {
        timeout: std::time::Duration::from_secs(contract.suite.limits.build_timeout_seconds),
        max_stream_bytes: contract.suite.limits.max_stream_bytes,
        cancellation: std::sync::Arc::clone(cancellation),
    });
    let cwd = std::env::current_dir()?;
    let environment = build_environment();
    let context = AdapterTools {
        cwd: &cwd,
        environment: &environment,
        runner: &runner,
    };
    if let Some(pipeline) = &mut bindings.pipeline {
        if pipeline.bash.path.as_os_str().is_empty() {
            pipeline.bash.path = context.find("bash")?;
        }
        if pipeline.cat.path.as_os_str().is_empty() {
            pipeline.cat.path = context.find("cat")?;
        }
    }
    let mut tools = cli_bench::replay_tools(&bundle)?;
    let harness = replay_harness()?;
    if let Some(engine) = &mut tools.engine {
        if let Some(path) = &args.hyperfine {
            engine.path = std::fs::canonicalize(path)?;
        } else if engine.path.as_os_str().is_empty() {
            engine.path = context.find("hyperfine")?;
        }
    } else {
        anyhow::ensure!(
            args.hyperfine.is_none(),
            "strict replay cannot add an engine"
        );
    }
    if let Some(time) = &mut tools.time
        && time.path.as_os_str().is_empty()
    {
        time.path = PathBuf::from("/usr/bin/time");
    }
    let recipe = cli_bench::replay_request(&bundle, &bindings)?;
    let mode = replay_mode(bundle.manifest.execution_kind, &tools)?;
    let host = collect_host(&contract.suite.environment);
    let store = Store::open(&args.data_dir)?;
    let replayed = recipe.execute(
        &cli_bench::ReplayContext {
            measurement_lock: &lock,
            harness: &harness,
            host: &host,
            mode,
            cache_root: &cwd.join("target/cli-bench"),
        },
        &store,
        &runner,
    )?;
    render(
        &replayed,
        report_format(&args.format),
        &mut std::io::stdout().lock(),
    )?;
    Ok(if replayed.result.outcome == RunOutcome::Complete {
        std::process::ExitCode::SUCCESS
    } else {
        std::process::ExitCode::FAILURE
    })
}

fn replay_harness() -> anyhow::Result<BoundTool> {
    let harness_path = std::env::current_exe()?;
    Ok(BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&harness_path)?,
            version: format!("cli-bench {}", env!("CARGO_PKG_VERSION")),
        },
        path: harness_path,
    })
}

fn replay_mode(
    kind: Option<cli_bench::ExecutionKind>,
    tools: &cli_bench::ReplayTools,
) -> anyhow::Result<RunMode<'_>> {
    Ok(match kind {
        Some(cli_bench::ExecutionKind::CheckOnly) => RunMode::CheckOnly {
            engine: tools.engine.as_ref(),
        },
        Some(cli_bench::ExecutionKind::Measure) => RunMode::Measure {
            engine: tools
                .engine
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("missing saved engine"))?,
            time: tools.time.as_ref(),
            platform: tools
                .platform
                .ok_or_else(|| anyhow::anyhow!("missing saved RSS platform"))?,
        },
        None => anyhow::bail!(
            "strict replay requires known execution kind; use run for a new experiment"
        ),
    })
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
    #[arg(
        short,
        long,
        conflicts_with = "suite",
        required_unless_present = "suite"
    )]
    pub package: Option<String>,
    #[arg(
        short,
        long,
        conflicts_with = "package",
        required_unless_present = "package"
    )]
    pub suite: Option<PathBuf>,
    #[arg(short = 'r', long, conflicts_with = "candidate")]
    pub candidate_ref: Option<String>,
    #[arg(short = 'a', long)]
    pub candidate: Option<PathBuf>,
    #[arg(short = 'b', long, conflicts_with = "previous")]
    pub previous_ref: Option<String>,
    #[arg(short = 'P', long)]
    pub previous: Option<PathBuf>,
    #[arg(short = 'x', long)]
    pub reference: Option<PathBuf>,
    #[arg(short = 'G', long, conflicts_with = "biggie")]
    pub generator_ref: Option<String>,
    #[arg(short = 'g', long)]
    pub biggie: Option<PathBuf>,
    #[arg(short = 'c', long = "case")]
    pub cases: Vec<String>,
    #[arg(short = 't', long)]
    pub toolchain: Option<String>,
    #[arg(short = 'F', long, value_delimiter = ',')]
    pub features: Vec<String>,
    #[arg(short = 'N', long)]
    pub no_default_features: bool,
    #[arg(short = 'm', long, default_value = "full", value_parser = ["full", "smoke"])]
    pub measurement_profile: String,
    #[arg(short = 'd', long, default_value = ".cli-bench")]
    pub data_dir: PathBuf,
    #[arg(short = 'M', long)]
    pub manifest_path: Option<PathBuf>,
    #[arg(short = 'f', long, default_value = "terminal", value_parser = ["terminal", "json", "markdown"])]
    pub format: String,
    #[arg(short = 'H', long)]
    pub hyperfine: Option<PathBuf>,
    #[arg(long, requires_all = ["hypothesis", "change_summary", "previous_ref"])]
    pub experiment: Option<String>,
    #[arg(long, requires = "experiment")]
    pub hypothesis: Option<String>,
    #[arg(long, requires = "experiment")]
    pub change_summary: Option<String>,
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
    with_signals(|cancellation| execute_build_inner(args, cancellation))
}
fn execute_build_inner(
    args: &BuildArgs,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<std::process::ExitCode> {
    use cli_bench::{
        BuildPolicy, BuildRequest, BuildTools, ExecutionPolicy, GitContext, ProcessRunner, Store,
    };
    use std::{io::Write, time::Duration};
    let measurement_lock = acquire_measurement_lock(cancellation)?;
    let cwd = std::env::current_dir()?;
    let scratch = AdapterScratch::new()?;
    let environment = build_environment();
    let runner = ProcessRunner::new(ExecutionPolicy {
        timeout: Duration::from_mins(30),
        max_stream_bytes: 268_435_456,
        cancellation: std::sync::Arc::clone(cancellation),
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
    let revision =
        cli_bench::resolve_revision(&measurement_lock, &cwd, &args.revision, &git, &runner)?;
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
        &measurement_lock,
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

fn with_signals(
    action: impl FnOnce(
        &std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> anyhow::Result<std::process::ExitCode>,
) -> anyhow::Result<std::process::ExitCode> {
    use std::sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };
    let cancellation = Arc::new(AtomicBool::new(false));
    let received = Arc::new(AtomicUsize::new(0));
    let mut handlers = vec![];
    for (signal, code) in [
        (signal_hook::consts::SIGINT, 130),
        (signal_hook::consts::SIGTERM, 143),
    ] {
        handlers.push(signal_hook::flag::register(
            signal,
            Arc::clone(&cancellation),
        )?);
        handlers.push(signal_hook::flag::register_usize(
            signal,
            Arc::clone(&received),
            code,
        )?);
    }
    let result = action(&cancellation);
    for handler in handlers {
        signal_hook::low_level::unregister(handler);
    }
    let code = received.load(Ordering::Relaxed);
    if code != 0 {
        if let Err(error) = result {
            use std::io::Write;
            writeln!(std::io::stderr().lock(), "{error}")?;
        }
        return Ok(std::process::ExitCode::from(u8::try_from(code)?));
    }
    result
}
pub fn execute_selection(
    args: &SelectionArgs,
    check_only: bool,
) -> anyhow::Result<std::process::ExitCode> {
    with_signals(|cancellation| execute_selection_inner(args, check_only, cancellation))
}
struct SelectionSetup {
    source: String,
    cwd: PathBuf,
    repository: PathBuf,
    suite: Suite,
    store: Store,
    experiment: Option<ExperimentRequest>,
}
struct SelectionResources {
    _scratch: AdapterScratch,
    runner: ProcessRunner,
    roles: RoleRequest,
    engine: Option<BoundTool>,
    time: Option<BoundTool>,
    platform: Platform,
    harness: BoundTool,
}
fn load_selection(
    args: &SelectionArgs,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<SelectionSetup> {
    let cwd = std::env::current_dir()?;
    let mut repository = if let Some(manifest) = &args.manifest_path {
        let path = std::fs::canonicalize(cwd.join(manifest))?;
        path.parent()
            .ok_or_else(|| anyhow::anyhow!("manifest has no parent"))?
            .to_path_buf()
    } else {
        cwd.clone()
    };
    let source = if let Some(path) = &args.suite {
        std::fs::read_to_string(cwd.join(path)).map_err(|error| {
            anyhow::anyhow!(
                "cannot load suite {}: {error}; provide a suite TOML path",
                path.display()
            )
        })?
    } else {
        let (workspace, source) = discovery::discover_package_suite(
            &cwd,
            args.manifest_path.as_deref(),
            args.package
                .as_deref()
                .ok_or_else(|| anyhow::anyhow!("package or suite selection required"))?,
            cancellation,
        )?;
        repository = workspace;
        source
    };
    let mut suite = parse_suite(&source)?;
    if !suite.allow_reference && args.reference.is_some() {
        anyhow::bail!("suite disallows reference roles; select a previous/candidate comparison");
    }
    if let Some(toolchain) = &args.toolchain {
        suite.build.toolchain = Some(toolchain.clone());
    }
    if !args.features.is_empty() {
        suite.build.features.clone_from(&args.features);
    }
    if args.no_default_features {
        suite.build.no_default_features = true;
    }
    validate_suite(&suite)?;
    let store = Store::open(&cwd.join(&args.data_dir))?;
    let experiment = args.experiment.as_ref().map(|id| ExperimentRequest {
        id: id.clone(),
        hypothesis: args.hypothesis.clone().unwrap_or_default(),
        change_summary: args.change_summary.clone().unwrap_or_default(),
        requested_previous: args.previous_ref.clone().unwrap_or_default(),
        requested_candidate: args.candidate_ref.clone().unwrap_or_else(|| {
            args.candidate
                .as_ref()
                .map_or_else(String::new, |p| p.display().to_string())
        }),
    });
    Ok(SelectionSetup {
        source,
        cwd,
        repository,
        suite,
        store,
        experiment,
    })
}
fn execute_selection_inner(
    args: &SelectionArgs,
    check_only: bool,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<std::process::ExitCode> {
    let measurement_lock = acquire_measurement_lock(cancellation)?;
    let setup = load_selection(args, cancellation)?;
    let SelectionSetup {
        suite,
        store,
        experiment,
        ..
    } = &setup;
    let resources = match prepare_selection(args, check_only, cancellation, &setup) {
        Ok(resources) => resources,
        Err(error) => {
            let mut writer = store.begin_resolved_run(&setup.source, suite, experiment.as_ref())?;
            writer.set_execution_kind(if check_only {
                cli_bench::ExecutionKind::CheckOnly
            } else {
                cli_bench::ExecutionKind::Measure
            });
            let bundle = writer.record_failure(RunOutcome::Failed, &error.to_string())?;
            render(
                &bundle,
                report_format(&args.format),
                &mut std::io::stdout().lock(),
            )?;
            return Ok(std::process::ExitCode::FAILURE);
        }
    };
    let SelectionResources {
        _scratch,
        runner,
        roles,
        engine,
        time,
        platform,
        harness,
    } = resources;
    let cases = args
        .cases
        .iter()
        .map(|id| CaseId::new(id.clone()))
        .collect::<Result<Vec<_>, _>>()?;
    let host = collect_host(&suite.environment);
    let mode = if check_only {
        RunMode::CheckOnly {
            engine: engine.as_ref(),
        }
    } else {
        RunMode::Measure {
            engine: engine
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("missing engine"))?,
            time: time.as_ref(),
            platform,
        }
    };
    let bundle = run(
        &RunRequest {
            submitted_toml: Some(&setup.source),
            preparation: ExperimentPreparation {
                run: &roles,
                suite,
                profile: if args.measurement_profile == "smoke" {
                    MeasurementProfile::Smoke
                } else {
                    MeasurementProfile::Full
                },
                selected_cases: &cases,
                expected_datasets: None,
            },
            measurement_lock: &measurement_lock,
            harness: &harness,
            host: &host,
            mode,
            experiment: experiment.as_ref(),
        },
        store,
        &runner,
    )?;
    render(
        &bundle,
        report_format(&args.format),
        &mut std::io::stdout().lock(),
    )?;
    Ok(if bundle.result.outcome == RunOutcome::Complete {
        std::process::ExitCode::SUCCESS
    } else {
        std::process::ExitCode::FAILURE
    })
}

fn prepare_selection(
    args: &SelectionArgs,
    check_only: bool,
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    setup: &SelectionSetup,
) -> anyhow::Result<SelectionResources> {
    let SelectionSetup {
        suite, repository, ..
    } = setup;
    let selected_cases = args
        .cases
        .iter()
        .map(|id| CaseId::new(id.clone()))
        .collect::<Result<Vec<_>, _>>()?;
    let mut selected_roles = vec![cli_bench::Role::Candidate];
    if args.previous.is_some() || args.previous_ref.is_some() {
        selected_roles.push(cli_bench::Role::Previous);
    }
    if args.reference.is_some() {
        selected_roles.push(cli_bench::Role::Reference);
    }
    cli_bench::validate_experiment_selection(
        suite,
        if args.measurement_profile == "smoke" {
            MeasurementProfile::Smoke
        } else {
            MeasurementProfile::Full
        },
        &selected_cases,
        &selected_roles,
    )?;
    let scratch = AdapterScratch::new()?;
    for directory in ["home", "config"] {
        std::fs::create_dir(scratch.0.join(directory))?;
    }
    let environment = build_environment();
    let runner = ProcessRunner::new(ExecutionPolicy {
        timeout: std::time::Duration::from_secs(suite.limits.build_timeout_seconds),
        max_stream_bytes: suite.limits.max_stream_bytes,
        cancellation: std::sync::Arc::clone(cancellation),
    });
    let context = AdapterTools {
        cwd: repository,
        environment: &environment,
        runner: &runner,
    };
    let harness_path = std::env::current_exe()?;
    let harness = BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&harness_path)?,
            version: format!("cli-bench {}", env!("CARGO_PKG_VERSION")),
        },
        path: harness_path,
    };
    let roles = select_roles(args, setup, &context, &scratch)?;
    let (engine, time, platform) = measurement_tools(args, check_only, setup, &context)?;
    Ok(SelectionResources {
        _scratch: scratch,
        runner,
        roles,
        engine,
        time,
        platform,
        harness,
    })
}
fn measurement_tools(
    args: &SelectionArgs,
    check_only: bool,
    setup: &SelectionSetup,
    context: &AdapterTools<'_>,
) -> anyhow::Result<(Option<BoundTool>, Option<BoundTool>, Platform)> {
    let SelectionSetup {
        cwd, experiment, ..
    } = setup;
    let engine = if !check_only || experiment.is_some() {
        Some(
            context.identify(
                &args
                    .hyperfine
                    .as_ref()
                    .map_or_else(|| context.find("hyperfine"), |p| Ok(cwd.join(p)))?,
                &["--version".into()],
            )?,
        )
    } else {
        None
    };
    let platform = if cfg!(target_os = "macos") {
        Platform::Darwin
    } else {
        Platform::Linux
    };
    let time = if check_only || !std::path::Path::new("/usr/bin/time").exists() {
        None
    } else {
        let path = PathBuf::from("/usr/bin/time");
        Some(if platform == Platform::Linux {
            context.identify(&path, &["--version".into()])?
        } else {
            BoundTool {
                identity: ToolIdentity {
                    file: fingerprint(&path)?,
                    version: "Darwin native /usr/bin/time -l; version unavailable".into(),
                },
                path,
            }
        })
    };
    Ok((engine, time, platform))
}
fn select_roles(
    args: &SelectionArgs,
    setup: &SelectionSetup,
    context: &AdapterTools<'_>,
    scratch: &AdapterScratch,
) -> anyhow::Result<RoleRequest> {
    let SelectionSetup {
        cwd,
        repository,
        suite,
        ..
    } = setup;
    let environment = context.environment;
    let need_build =
        args.candidate_ref.is_some() || args.previous_ref.is_some() || args.biggie.is_none();
    let git = if need_build {
        Some(GitContext {
            tool: context.identify(&context.find("git")?, &["--version".into()])?,
            environment: environment.clone(),
            scratch_root: scratch.0.clone(),
        })
    } else {
        None
    };
    let tools = if need_build {
        Some(context.build_tools(suite.build.toolchain.as_deref())?)
    } else {
        None
    };
    let pipeline = if suite
        .cases
        .iter()
        .any(|case| matches!(case.io.stdin, StdinPolicy::Pipe { .. }))
    {
        Some(PipelineTools {
            bash: context.identify(&context.find("bash")?, &["--version".into()])?,
            cat: BoundTool {
                path: context.find("cat")?,
                identity: ToolIdentity {
                    file: fingerprint(&context.find("cat")?)?,
                    version: "system cat; version unavailable".into(),
                },
            },
        })
    } else {
        None
    };
    let source =
        |revision: &Option<String>, path: &Option<PathBuf>| -> anyhow::Result<ExecutableSource> {
            if let Some(revision) = revision {
                Ok(ExecutableSource::Revision(revision.clone()))
            } else {
                Ok(ExecutableSource::Prebuilt(
                    cwd.join(
                        path.as_ref()
                            .ok_or_else(|| anyhow::anyhow!("missing executable selection"))?,
                    ),
                ))
            }
        };
    let roles = RoleRequest {
        repository: repository.clone(),
        candidate: source(&args.candidate_ref, &args.candidate)?,
        previous: if args.previous_ref.is_some() || args.previous.is_some() {
            Some(source(&args.previous_ref, &args.previous)?)
        } else {
            None
        },
        reference: args.reference.as_ref().map(|p| cwd.join(p)),
        generator: if args.generator_ref.is_some() || args.biggie.is_some() {
            Some(source(&args.generator_ref, &args.biggie)?)
        } else {
            None
        },
        git,
        tools,
        cache_root: repository.join("target/cli-bench"),
        home: scratch.0.join("home"),
        config: scratch.0.join("config"),
        pipeline,
    };
    Ok(roles)
}
fn report_format(value: &str) -> cli_bench::ReportFormat {
    match value {
        "json" => cli_bench::ReportFormat::Json,
        "markdown" => cli_bench::ReportFormat::Markdown,
        _ => cli_bench::ReportFormat::Terminal,
    }
}
fn acquire_measurement_lock(
    cancellation: &std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<cli_bench::MeasurementLock> {
    use std::io::Write;
    cli_bench::MeasurementLock::acquire(cancellation, || {
        let mut diagnostic = std::io::stderr().lock();
        writeln!(
            diagnostic,
            "Waiting for the per-user cli-bench measurement lock..."
        )?;
        diagnostic.flush()?;
        Ok(())
    })
    .map_err(Into::into)
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
    fn build_tools(&self, toolchain: Option<&str>) -> anyhow::Result<cli_bench::BuildTools> {
        let rustup = self.find("rustup")?;
        let selected = if let Some(value) = toolchain {
            value.to_string()
        } else {
            self.run(&rustup, &["show".into(), "active-toolchain".into()])?
                .split_whitespace()
                .next()
                .ok_or_else(|| anyhow::anyhow!("no active installed toolchain"))?
                .into()
        };
        let locate = |name: &str| -> anyhow::Result<cli_bench::BoundTool> {
            let path = self.run(
                &rustup,
                &[
                    "which".into(),
                    "--toolchain".into(),
                    selected.clone(),
                    name.into(),
                ],
            )?;
            self.identify(&std::fs::canonicalize(path.trim())?, &["-Vv".into()])
        };
        Ok(cli_bench::BuildTools {
            cargo: locate("cargo")?,
            rustc: locate("rustc")?,
            environment: self.environment.clone(),
        })
    }
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
    fn package_selection_and_previous_short_flags_are_unambiguous() {
        for command in ["run", "check"] {
            assert!(
                Cli::try_parse_validated_from([
                    "cli-bench",
                    command,
                    "-p",
                    "tailr",
                    "-a",
                    "/candidate",
                    "-P",
                    "/previous"
                ])
                .is_ok()
            );
            assert_eq!(
                Cli::try_parse_validated_from([
                    "cli-bench",
                    command,
                    "-p",
                    "tailr",
                    "-s",
                    "custom.toml",
                    "-a",
                    "/candidate",
                    "-P",
                    "/previous"
                ])
                .err()
                .map(|error| error.kind()),
                Some(clap::error::ErrorKind::ArgumentConflict)
            );
            assert_eq!(
                Cli::try_parse_validated_from([
                    "cli-bench",
                    command,
                    "-a",
                    "/candidate",
                    "-P",
                    "/previous"
                ])
                .err()
                .map(|error| error.kind()),
                Some(clap::error::ErrorKind::MissingRequiredArgument)
            );
        }
        assert!(
            Cli::try_parse_validated_from([
                "cli-bench",
                "replay",
                "-i",
                "bundle",
                "-P",
                "/previous"
            ])
            .is_ok()
        );
        assert!(
            Cli::try_parse_validated_from(["cli-bench", "build", "-p", "tailr", "-r", "HEAD"])
                .is_ok()
        );
    }

    #[test]
    fn cli_rejects_disallowed_reference_before_creating_store_or_discovering_tools()
    -> anyhow::Result<()> {
        let root = assert_fs::TempDir::new()?;
        let custom = root.path().join("custom.toml");
        std::fs::write(
            &custom,
            format!(
                "allow_reference = false\n{}",
                include_str!("../tests/inputs/minimal-suite.toml")
            ),
        )?;
        let selection = custom.to_string_lossy().into_owned();
        let (cli, _) = Cli::try_parse_validated_from([
            "cli-bench",
            "check",
            "-s",
            &selection,
            "-a",
            "/missing-candidate",
            "-P",
            "/missing-previous",
            "-x",
            "/missing-reference",
            "-g",
            "/missing-generator",
        ])?;
        let Some(super::Command::Check(mut args)) = cli.command else {
            anyhow::bail!("check command missing");
        };
        args.data_dir = root.path().join("uncreated-store");
        let error = super::load_selection(
            &args,
            &std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        )
        .err()
        .ok_or_else(|| anyhow::anyhow!("reference was accepted"))?;
        anyhow::ensure!(
            error
                .to_string()
                .contains("suite disallows reference roles")
        );
        anyhow::ensure!(!args.data_dir.exists());
        Ok(())
    }

    #[test]
    fn run_and_check_reject_conflicting_role_bindings() {
        for command in ["run", "check"] {
            for conflicting in [
                vec!["-r", "HEAD", "-a", "/candidate", "-x", "/reference"],
                vec!["-a", "/candidate", "-b", "HEAD~1", "-P", "/previous"],
                vec![
                    "-a",
                    "/candidate",
                    "-P",
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
