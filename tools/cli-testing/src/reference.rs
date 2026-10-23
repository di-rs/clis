//! Independent reference execution; normal catalog tests never call this module.
use crate::{
    Error, Result,
    catalog::{Case, Input, Suite},
    context::{capture, check_bytes, check_status},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::PathBuf,
    process::{Output, Stdio},
};

/// Identity of the executable actually used for a reference capture.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub program: PathBuf,
    pub sha256: String,
    pub platform: String,
    pub os_release: String,
    pub version: Option<String>,
}

/// A captured independent result and the effective invocation metadata.
pub struct Capture {
    pub identity: Identity,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub output: Output,
}

/// Execute the selected reference with the case's inputs and explicit overrides.
/// # Errors
/// Rejects unavailable/wrong-platform references and unexpected status or stderr.
pub fn run(suite: &Suite, case: &Case) -> Result<Capture> {
    let name = case
        .origin
        .reference
        .as_ref()
        .ok_or_else(|| Error::new(format!("case {:?} has a manual expectation", case.name)))?;
    let reference = suite
        .references
        .get(name)
        .ok_or_else(|| Error::new(format!("unknown reference {name:?}")))?;
    if reference.platform != std::env::consts::OS {
        return Err(Error::new(format!(
            "reference {name:?} requires {}, this host is {}; no fallback is used",
            reference.platform,
            std::env::consts::OS
        )));
    }
    let program = reference.program.canonicalize().map_err(|error| {
        Error::caused_by(
            format!(
                "reference {name:?} is unavailable at {}",
                reference.program.display()
            ),
            error,
        )
    })?;
    if program.file_name().and_then(|name| name.to_str()) == Some(suite.binary.as_str()) {
        return Err(Error::new(
            "reference executable must not be the candidate binary",
        ));
    }
    let identity = identity(&program, &reference.version_args)?;
    let args = case
        .origin
        .args
        .clone()
        .unwrap_or_else(|| case.args.clone());
    let mut env = crate::default_environment();
    env.extend(suite.env.clone());
    env.extend(case.env.clone());
    env.extend(reference.env.clone());
    env.extend(case.origin.env.clone());
    let mut command = crate::std_command(&program, suite.package_dir());
    command.args(&args).envs(&env);
    let input = match &case.stdin {
        Some(Input::File { path }) => {
            command.stdin(fs::File::open(suite.fixture_path(path)?)?);
            None
        }
        Some(input @ Input::Pipe { .. }) => Some(input.bytes(suite)?),
        None => {
            command.stdin(Stdio::null());
            None
        }
    };
    let output = capture(command, input)?;
    check_status(
        &case.name,
        &output,
        case.origin.exit.unwrap_or(case.expect.exit),
    )?;
    let (_, stderr) = case.expect.bytes(suite)?;
    let stderr = case
        .origin
        .stderr
        .as_ref()
        .map_or(stderr, |text| text.as_bytes().to_vec());
    check_bytes(&case.name, "reference stderr", &output.stderr, &stderr)?;
    Ok(Capture {
        identity,
        args,
        env,
        output,
    })
}

pub(crate) fn hash(bytes: &[u8]) -> String {
    use std::fmt::Write;
    let mut hex = String::new();
    for byte in Sha256::digest(bytes) {
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

/// Compare an automatic case with its independent reference and committed contract.
/// # Errors
/// Reports invalid ownership, candidate/reference failures, or differing output.
pub fn compare(suite: &Suite, case: &Case, candidate: &std::path::Path) -> Result {
    if case.execution == crate::catalog::Execution::Rust {
        return Err(Error::new(format!(
            "case {:?} requires native Rust tests",
            case.name
        )));
    }
    if let Some(name) = &case.origin.reference {
        let reference = suite
            .references
            .get(name)
            .ok_or_else(|| Error::new("unknown reference"))?;
        if hash(&fs::read(candidate)?) == hash(&fs::read(&reference.program)?) {
            return Err(Error::new(
                "candidate and reference must be independent executables",
            ));
        }
    }
    let test = crate::TestContext::new(candidate, suite.package_dir());
    let mut context = crate::Context::prepare(test, suite, case)?;
    context.check()?;
    if case.origin.reference.is_some() {
        let captured = run(suite, case)?;
        let mut expected = captured.output.stdout;
        if let Some(adapt) = &case.adapt {
            expected = adapt.expected_stdout.apply(&expected);
        }
        check_bytes(
            &case.name,
            "reference vs candidate stdout",
            &context.run()?.stdout,
            &expected,
        )?;
    }
    Ok(())
}

pub(crate) fn identity(program: &std::path::Path, version_args: &[String]) -> Result<Identity> {
    let (release_tool, release_args): (&str, &[&str]) = if cfg!(target_os = "macos") {
        ("/usr/bin/sw_vers", &[])
    } else {
        ("/usr/bin/uname", &["-sr"])
    };
    let mut command = crate::std_command(release_tool, ".");
    command.args(release_args).stdin(Stdio::null());
    let release = capture(command, None)?;
    check_status("reference identity", &release, 0)?;
    let version = if version_args.is_empty() {
        None
    } else {
        let mut command = crate::std_command(program, ".");
        command.args(version_args).stdin(Stdio::null());
        let output = capture(command, None)?;
        check_status("reference version", &output, 0)?;
        Some(
            String::from_utf8(output.stdout)
                .map_err(|error| Error::caused_by("reference version is not UTF-8", error))?,
        )
    };
    Ok(Identity {
        program: program.to_path_buf(),
        sha256: hash(&fs::read(program)?),
        platform: std::env::consts::OS.to_owned(),
        os_release: String::from_utf8(release.stdout)
            .map_err(|error| Error::caused_by("OS release is not UTF-8", error))?,
        version,
    })
}
