use crate::{
    ArtifactRecord, BenchError, CaseSpec, CommandSpec, InputRecord, MeasurementProfile, Role,
    ToolIdentity,
};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

/// A retained executable and its provenance, resolved independently of active role.
#[derive(Clone, Debug)]
pub struct BoundExecutable {
    pub path: PathBuf,
    pub artifact: ArtifactRecord,
}
/// A resolved tool path and observed identity; discovery belongs to the caller.
#[derive(Clone, Debug)]
pub struct BoundTool {
    pub path: PathBuf,
    pub identity: ToolIdentity,
}
/// The explicitly identified programs used by the fixed finite pipeline.
#[derive(Clone, Debug)]
pub struct PipelineTools {
    pub bash: BoundTool,
    pub cat: BoundTool,
}
/// The complete selection, child settings and caller-owned isolated config roots.
#[derive(Clone, Debug)]
pub struct RoleBindings {
    pub roles: BTreeMap<Role, BoundExecutable>,
    /// Retained fixture generator; invocation-only callers may omit it.
    pub generator: Option<BoundExecutable>,
    pub environment: BTreeMap<String, String>,
    pub home: PathBuf,
    pub config: PathBuf,
    pub pipeline: Option<PipelineTools>,
}
/// Input bindings with optional generation evidence. Literal fixtures omit evidence.
#[derive(Clone, Debug, Default)]
pub struct DatasetSet {
    pub inputs: BTreeMap<String, InputRecord>,
    pub generations: BTreeMap<String, crate::GenerationRecord>,
}
/// Scope of a command, including the shell/producer cost of pipe workloads.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InvocationScope {
    Direct,
    Pipeline,
}
/// One resolved role/profile invocation; callers preserve this same timing boundary.
#[derive(Clone, Debug)]
pub struct Invocation {
    pub role: Role,
    pub profile: MeasurementProfile,
    pub command: CommandSpec,
    pub expected_status: i32,
    pub scope: InvocationScope,
}
/// Resolve exactly one role/profile and whole-argument tokens without executing tools.
///
/// # Errors
/// Rejects invalid cases, missing bindings, changed inputs/tools, unsupported paths,
/// unsafe path records and scratch symlinks. Reads/hashes bound files, retaining
/// memory proportional to the expanded argv. Does not create directories or mutate
/// the caller's case, bindings, datasets or environment.
pub fn resolve_invocation(
    case: &CaseSpec,
    role: Role,
    profile: MeasurementProfile,
    bindings: &RoleBindings,
    datasets: &DatasetSet,
    scratch: &Path,
) -> Result<Invocation, BenchError> {
    crate::suite::validate_strings(&serde_json::to_value(case)?)?;
    crate::suite::validate_case(case, &datasets.inputs.keys().map(String::as_str).collect())?;
    directory(scratch)?;
    directory(&bindings.home)?;
    directory(&bindings.config)?;
    let executable = bindings
        .roles
        .get(&role)
        .ok_or_else(|| BenchError::invalid(format!("unbound role {role:?}")))?;
    crate::process::utf8_path(&executable.path)?;
    crate::verify_file(&executable.path, &executable.artifact.file)?;
    let overrides = case.profiles.get(&profile);
    let common = overrides
        .and_then(|value| value.argv.as_ref())
        .unwrap_or(&case.argv);
    let role_argv = overrides
        .and_then(|value| value.role_argv.as_ref())
        .unwrap_or(&case.role_argv);
    let arguments = role_argv.get(&role).unwrap_or(common);
    let argv = resolve_arguments(arguments, datasets, scratch)?;
    let stdout = match &case.io.stdout {
        crate::StdoutPolicy::Discard {} => crate::CommandOutput::Discard,
        crate::StdoutPolicy::DrainedPipe {} => crate::CommandOutput::DrainedPipe,
        crate::StdoutPolicy::ScratchFile { path } => {
            crate::CommandOutput::File(contained(scratch, path)?)
        }
    };
    let mut command = CommandSpec {
        program: executable.path.clone(),
        argv,
        cwd: scratch.into(),
        environment: child_environment(bindings)?,
        stdin: crate::CommandInput::Null,
        stdout,
    };
    let scope = match &case.io.stdin {
        crate::StdinPolicy::Null {} => InvocationScope::Direct,
        crate::StdinPolicy::RegularFile { dataset } => {
            command.stdin = crate::CommandInput::File(input_path(datasets, dataset)?.into());
            InvocationScope::Direct
        }
        crate::StdinPolicy::Pipe { dataset } => {
            let input = input_path(datasets, dataset)?;
            let tools = bindings.pipeline.as_ref().ok_or_else(|| {
                BenchError::invalid("pipe input requires identified Bash and cat bindings")
            })?;
            for tool in [&tools.bash, &tools.cat] {
                crate::process::utf8_path(&tool.path)?;
                if tool.identity.version.trim().is_empty() || tool.identity.version.contains('\0') {
                    return Err(BenchError::invalid(
                        "pipeline tool requires an observed version",
                    ));
                }
                crate::verify_file(&tool.path, &tool.identity.file)?;
            }
            // Only this constant is shell source. Every path/argument is positional.
            let mut pipeline = [
                "--noprofile",
                "--norc",
                "-o",
                "pipefail",
                "-c",
                "\"$1\" -- \"$2\" | \"${@:3}\"",
                "cli-bench-pipeline",
            ]
            .map(str::to_owned)
            .to_vec();
            pipeline.push(crate::process::utf8_path(&tools.cat.path)?.into());
            pipeline.push(crate::process::utf8_path(input)?.into());
            pipeline.push(crate::process::utf8_path(&command.program)?.into());
            pipeline.append(&mut command.argv);
            command.program.clone_from(&tools.bash.path);
            command.argv = pipeline;
            InvocationScope::Pipeline
        }
    };
    Ok(Invocation {
        role,
        profile,
        command,
        expected_status: case.expected_status,
        scope,
    })
}
pub fn directory(path: &Path) -> Result<(), BenchError> {
    crate::process::utf8_path(path)?;
    if !std::fs::symlink_metadata(path)?.is_dir() {
        return Err(BenchError::invalid(
            "execution roots must be real, non-symlink directories",
        ));
    }
    Ok(())
}
pub fn child_environment(bindings: &RoleBindings) -> Result<BTreeMap<String, String>, BenchError> {
    let mut environment = BTreeMap::from([
        ("LC_ALL".into(), "C".into()),
        ("TZ".into(), "UTC".into()),
        ("CLIS_LOG_LEVEL".into(), "off".into()),
    ]);
    for (key, value) in &bindings.environment {
        if !matches!(key.as_str(), "LC_ALL" | "TZ" | "CLIS_LOG_LEVEL") || value.contains('\0') {
            return Err(BenchError::invalid("unsupported invocation environment"));
        }
        environment.insert(key.clone(), value.clone());
    }
    environment.insert(
        "HOME".into(),
        crate::process::utf8_path(&bindings.home)?.into(),
    );
    environment.insert(
        "XDG_CONFIG_HOME".into(),
        crate::process::utf8_path(&bindings.config)?.into(),
    );
    Ok(environment)
}
fn input_path<'a>(datasets: &'a DatasetSet, id: &str) -> Result<&'a Path, BenchError> {
    let input = datasets
        .inputs
        .get(id)
        .ok_or_else(|| BenchError::invalid(format!("unbound dataset {id:?}")))?;
    if input.dataset != id {
        return Err(BenchError::invalid("dataset key/identity mismatch"));
    }
    crate::process::utf8_path(&input.path)?;
    crate::verify_file(&input.path, &input.file)?;
    Ok(&input.path)
}
fn contained(root: &Path, relative: &str) -> Result<PathBuf, BenchError> {
    crate::suite::validate_path(relative)?;
    let mut path = root.to_path_buf();
    for component in relative.split('/') {
        path.push(component);
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(BenchError::invalid(
                    "scratch paths must not traverse symlinks",
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(path)
}
fn resolve_arguments(
    arguments: &[String],
    datasets: &DatasetSet,
    scratch: &Path,
) -> Result<Vec<String>, BenchError> {
    use crate::suite::ArgumentToken;
    let mut argv = Vec::new();
    for argument in arguments {
        match crate::suite::parse_argument(argument)? {
            ArgumentToken::Literal => argv.push(
                argument
                    .strip_prefix("@@")
                    .map_or_else(|| argument.clone(), |suffix| format!("@{suffix}")),
            ),
            ArgumentToken::Input(id) => {
                argv.push(crate::process::utf8_path(input_path(datasets, id)?)?.into());
            }
            ArgumentToken::Scratch(path) => {
                argv.push(crate::process::utf8_path(&contained(scratch, path)?)?.into());
            }
            ArgumentToken::Records(id) => {
                resolve_records(input_path(datasets, id)?, scratch, &mut argv)?;
            }
            ArgumentToken::Output => {
                return Err(BenchError::invalid(
                    "@output is only valid during generation",
                ));
            }
        }
    }
    Ok(argv)
}
fn resolve_records(path: &Path, scratch: &Path, argv: &mut Vec<String>) -> Result<(), BenchError> {
    let reader = std::io::BufReader::new(std::fs::File::open(path)?);
    for record in crate::dataset::decode_path_records(reader)? {
        argv.push(crate::process::utf8_path(&contained(scratch, &record)?)?.into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{CaseOverride, StdoutPolicy, fingerprint, parse_suite};
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    fn setup(root: &Path) -> Result<(CaseSpec, RoleBindings, DatasetSet), BenchError> {
        let suite = parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let case = suite
            .cases
            .into_iter()
            .next()
            .ok_or_else(|| BenchError::invalid("missing fixture"))?;
        let program = root.join("program");
        std::fs::write(&program, b"fixture executable identity")?;
        let artifact = ArtifactRecord {
            schema_version: 1,
            id: "fixture".into(),
            file: fingerprint(&program)?,
            build: None,
        };
        let roles = BTreeMap::from([(
            Role::Candidate,
            BoundExecutable {
                path: program,
                artifact,
            },
        )]);
        let input = root.join("input");
        std::fs::write(&input, b"a/b\nc/d\n")?;
        let inputs = BTreeMap::from([(
            "tiny".into(),
            InputRecord {
                dataset: "tiny".into(),
                path: input.clone(),
                file: fingerprint(&input)?,
            },
        )]);
        std::fs::create_dir(root.join("home"))?;
        std::fs::create_dir(root.join("config"))?;
        let bindings = RoleBindings {
            generator: None,
            roles,
            environment: BTreeMap::from([
                ("LC_ALL".into(), "C".into()),
                ("TZ".into(), "UTC".into()),
                ("CLIS_LOG_LEVEL".into(), "off".into()),
            ]),
            home: root.join("home"),
            config: root.join("config"),
            pipeline: None,
        };
        Ok((
            case,
            bindings,
            DatasetSet {
                inputs,
                ..DatasetSet::default()
            },
        ))
    }
    #[test]
    fn resolves_whole_tokens_and_preserves_literal_arguments() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let (mut case, bindings, datasets) = setup(root.path())?;
        case.argv = [
            "",
            "a b",
            "'quoted'",
            "$(touch injected)",
            "@@input:tiny",
            "embedded@input:tiny",
            "@input:tiny",
            "@scratch:a/b",
            "@records:tiny",
        ]
        .map(str::to_owned)
        .into();
        let invocation = resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )?;
        let mut expected: Vec<String> = [
            "",
            "a b",
            "'quoted'",
            "$(touch injected)",
            "@input:tiny",
            "embedded@input:tiny",
        ]
        .map(str::to_owned)
        .into();
        for suffix in ["input", "a/b", "a/b", "c/d"] {
            expected.push(
                root.join(suffix)
                    .to_str()
                    .ok_or("non-UTF-8 fixture")?
                    .into(),
            );
        }
        if invocation.command.argv != expected {
            return Err(format!("unexpected argv: {:?}", invocation.command.argv).into());
        }
        Ok(())
    }
    #[test]
    fn explicit_profile_replaces_role_map_then_falls_back_to_common_argv() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let (mut case, bindings, datasets) = setup(root.path())?;
        case.role_argv
            .insert(Role::Candidate, vec!["full role".into()]);
        case.profiles.insert(
            MeasurementProfile::Smoke,
            CaseOverride {
                argv: Some(vec!["smoke common".into()]),
                role_argv: Some(BTreeMap::new()),
                correctness: None,
                work: None,
            },
        );
        let full = resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )?;
        let smoke = resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Smoke,
            &bindings,
            &datasets,
            root.path(),
        )?;
        if full.command.argv != ["full role"] || smoke.command.argv != ["smoke common"] {
            return Err("profile precedence lost".into());
        }
        if resolve_invocation(
            &case,
            Role::Reference,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("unbound role accepted".into());
        }
        Ok(())
    }
    #[test]
    fn rejects_escaping_records_and_existing_scratch_symlinks() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let (mut case, bindings, mut datasets) = setup(root.path())?;
        case.argv = vec!["@records:tiny".into()];
        for bytes in [
            b"../escape\n".as_slice(),
            b"/absolute\n",
            b"\n",
            b"bad\r\n",
            b"bad\0\n",
            b"\xff\n",
            b"missing-newline",
        ] {
            let input = datasets.inputs.get_mut("tiny").ok_or("missing input")?;
            std::fs::write(&input.path, bytes)?;
            input.file = fingerprint(&input.path)?;
            if resolve_invocation(
                &case,
                Role::Candidate,
                MeasurementProfile::Full,
                &bindings,
                &datasets,
                root.path(),
            )
            .is_ok()
            {
                return Err(format!("invalid path record accepted: {bytes:?}").into());
            }
        }
        std::os::unix::fs::symlink("/tmp", root.join("escape"))?;
        case.argv = vec!["@scratch:escape/child".into()];
        if resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("scratch symlink followed".into());
        }
        case.argv.clear();
        case.io.stdout = StdoutPolicy::ScratchFile {
            path: "escape/output".into(),
        };
        if resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("sink symlink followed".into());
        }
        Ok(())
    }
    #[test]
    fn refuses_changed_inputs_unbound_tools_and_ambient_shell_environment() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let (mut case, mut bindings, datasets) = setup(root.path())?;
        resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )?;
        bindings
            .environment
            .insert("BASH_ENV".into(), "/untrusted/startup".into());
        if resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("shell startup environment accepted".into());
        }
        bindings.environment.remove("BASH_ENV");
        case.io.stdin = crate::StdinPolicy::Pipe {
            dataset: "tiny".into(),
        };
        if resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("unidentified pipeline tools accepted".into());
        }
        case.io.stdin = crate::StdinPolicy::Null {};
        std::fs::write(root.join("input"), b"changed")?;
        if resolve_invocation(
            &case,
            Role::Candidate,
            MeasurementProfile::Full,
            &bindings,
            &datasets,
            root.path(),
        )
        .is_ok()
        {
            return Err("changed input identity accepted".into());
        }
        Ok(())
    }
}
