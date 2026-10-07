#![allow(
    dead_code,
    reason = "shared build fixtures are used differently by CLI and library integration tests"
)]
use super::bench_api as cli_bench;
use cli_bench::*;
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, atomic::AtomicBool},
    time::Duration,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

pub fn command(cwd: &Path, program: &Path, args: &[&str]) -> Result<String> {
    let environment = ["PATH", "HOME", "CARGO_HOME", "RUSTUP_HOME", "TMPDIR"]
        .into_iter()
        .filter_map(|key| std::env::var_os(key).map(|value| (key, value)));
    let output = Command::new(program)
        .env_clear()
        .envs(environment)
        .env("RUSTUP_AUTO_INSTALL", "0")
        .current_dir(cwd)
        .args(args)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_AUTHOR_NAME", "fixture")
        .env("GIT_AUTHOR_EMAIL", "fixture@example.invalid")
        .env("GIT_COMMITTER_NAME", "fixture")
        .env("GIT_COMMITTER_EMAIL", "fixture@example.invalid")
        .output()?;
    if !output.status.success() {
        return Err(format!(
            "{} failed: {}",
            program.display(),
            String::from_utf8(output.stderr)?
        )
        .into());
    }
    Ok(String::from_utf8(output.stdout)?.trim().into())
}

pub fn runner() -> ProcessRunner {
    ProcessRunner::new(ExecutionPolicy {
        timeout: Duration::from_secs(60),
        max_stream_bytes: 8 * 1024 * 1024,
        cancellation: Arc::new(AtomicBool::new(false)),
    })
}

pub struct Fixture {
    pub root: assert_fs::TempDir,
    pub repo: PathBuf,
    pub git: GitContext,
    pub tools: BuildTools,
}
impl Fixture {
    pub fn new() -> Result<Self> {
        let root = assert_fs::TempDir::new()?;
        let repo = root.path().join("repo");
        std::fs::create_dir(&repo)?;
        std::fs::create_dir(repo.join("src"))?;
        let scratch = root.path().join("scratch");
        std::fs::create_dir(&scratch)?;
        let git_path = PathBuf::from("/usr/bin/git");
        command(&repo, &git_path, &["init", "--quiet"])?;
        std::fs::write(
            repo.join("Cargo.toml"),
            "[package]\nname='tiny'\nversion='0.1.0'\nedition='2024'\n[features]\nfast=[]\n",
        )?;
        std::fs::write(
            repo.join("Cargo.lock"),
            "version = 4\n[[package]]\nname = \"tiny\"\nversion = \"0.1.0\"\n",
        )?;
        std::fs::write(
            repo.join("src/main.rs"),
            "fn main() { println!(\"first\"); }\n",
        )?;
        command(&repo, &git_path, &["add", "."])?;
        command(&repo, &git_path, &["commit", "--quiet", "-m", "first"])?;
        let environment = BTreeMap::from([
            ("PATH".into(), std::env::var("PATH")?),
            ("LC_ALL".into(), "C".into()),
            ("GIT_CONFIG_GLOBAL".into(), "/dev/null".into()),
            ("GIT_CONFIG_NOSYSTEM".into(), "1".into()),
        ]);
        let git = GitContext {
            tool: BoundTool {
                identity: ToolIdentity {
                    file: fingerprint(&git_path)?,
                    version: command(&repo, &git_path, &["--version"])?,
                },
                path: git_path,
            },
            environment: environment.clone(),
            scratch_root: scratch,
        };
        let rustup = PathBuf::from("rustup");
        // `which` only locates installed tools; it cannot install a toolchain.
        let cwd = std::env::current_dir()?;
        let cargo = PathBuf::from(command(&cwd, &rustup, &["which", "cargo"])?);
        let rustc = PathBuf::from(command(&cwd, &rustup, &["which", "rustc"])?);
        let tools = BuildTools {
            cargo: BoundTool {
                identity: ToolIdentity {
                    file: fingerprint(&cargo)?,
                    version: command(&repo, &cargo, &["-Vv"])?,
                },
                path: cargo,
            },
            rustc: BoundTool {
                identity: ToolIdentity {
                    file: fingerprint(&rustc)?,
                    version: command(&repo, &rustc, &["-Vv"])?,
                },
                path: rustc,
            },
            environment,
        };
        Ok(Self {
            root,
            repo,
            git,
            tools,
        })
    }
    pub fn request(&self, revision: ResolvedRevision) -> BuildRequest {
        BuildRequest {
            repository: self.repo.clone(),
            revision,
            package: "tiny".into(),
            binary: Some("tiny".into()),
            policy: BuildPolicy::default(),
            git: self.git.clone(),
            tools: self.tools.clone(),
            cache_root: self.repo.join("target/cli-bench"),
        }
    }
}
