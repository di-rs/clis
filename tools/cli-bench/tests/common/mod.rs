use cli_bench::{CapturePaths, CommandInput, CommandOutput, CommandSpec, ExecutionPolicy};
use std::{
    collections::BTreeMap,
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    sync::{Arc, atomic::AtomicBool},
    time::Duration,
};
pub type TestResult = Result<(), Box<dyn std::error::Error>>;

pub fn fixture(root: &Path, name: &str, source: &str) -> std::io::Result<PathBuf> {
    let path = root.join(name);
    fs::write(&path, format!("#!/bin/sh\n{source}\n"))?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
    Ok(path)
}
pub fn command(root: &Path, program: PathBuf) -> CommandSpec {
    CommandSpec {
        program,
        argv: vec![],
        cwd: root.into(),
        environment: BTreeMap::from([
            ("LC_ALL".into(), "C".into()),
            ("PATH".into(), "/usr/bin:/bin".into()),
        ]),
        stdin: CommandInput::Null,
        stdout: CommandOutput::Capture,
    }
}
pub fn captures(root: &Path) -> CapturePaths {
    CapturePaths {
        stdout: root.join("stdout"),
        stderr: root.join("stderr"),
    }
}
pub fn policy() -> ExecutionPolicy {
    ExecutionPolicy {
        timeout: Duration::from_secs(3),
        max_stream_bytes: 1024 * 1024,
        cancellation: Arc::new(AtomicBool::new(false)),
    }
}

pub fn invocation_inputs(
    root: &Path,
    program: PathBuf,
) -> Result<
    (
        cli_bench::CaseSpec,
        cli_bench::RoleBindings,
        cli_bench::DatasetSet,
    ),
    Box<dyn std::error::Error>,
> {
    use cli_bench::{
        ArtifactRecord, BoundExecutable, BoundTool, DatasetSet, InputRecord, PipelineTools, Role,
        RoleBindings, ToolIdentity, fingerprint, parse_suite,
    };
    let suite = parse_suite(include_str!("../inputs/minimal-suite.toml"))?;
    let mut case = suite
        .cases
        .into_iter()
        .next()
        .ok_or("missing fixture case")?;
    case.argv.clear();
    let artifact = ArtifactRecord {
        schema_version: 1,
        id: "fixture".into(),
        file: fingerprint(&program)?,
        build: None,
    };
    let input = root.join("input ' ; $.bin");
    fs::write(&input, b"small explicit fixture\0\xff")?;
    let inputs = BTreeMap::from([(
        "tiny".into(),
        InputRecord {
            dataset: "tiny".into(),
            path: input.clone(),
            file: fingerprint(&input)?,
        },
    )]);
    let tool = |path: &str| -> Result<BoundTool, Box<dyn std::error::Error>> {
        Ok(BoundTool {
            path: path.into(),
            identity: ToolIdentity {
                file: fingerprint(Path::new(path))?,
                version: "native fixture dependency".into(),
            },
        })
    };
    fs::create_dir(root.join("home"))?;
    fs::create_dir(root.join("config"))?;
    let bindings = RoleBindings {
        roles: BTreeMap::from([(
            Role::Candidate,
            BoundExecutable {
                path: program,
                artifact,
            },
        )]),
        environment: suite.environment,
        home: root.join("home"),
        config: root.join("config"),
        pipeline: Some(PipelineTools {
            bash: tool("/bin/bash")?,
            cat: tool("/bin/cat")?,
        }),
    };
    Ok((case, bindings, DatasetSet { inputs }))
}
