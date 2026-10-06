use cli_bench::*;
use std::{
    fs,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    sync::{Arc, atomic::AtomicBool},
    time::Duration,
};
pub type TestResult = Result<(), Box<dyn std::error::Error>>;
pub struct Fixture {
    pub root: assert_fs::TempDir,
    pub suite: Suite,
    pub request: RunRequest,
    pub store: Store,
    pub runner: ProcessRunner,
}
pub fn script(root: &Path, name: &str, body: &str) -> Result<PathBuf, BenchError> {
    let path = root.join(name);
    fs::write(&path, format!("#!/bin/sh\n{body}\n"))?;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
    Ok(path)
}
impl Fixture {
    pub fn new(candidate: &str, previous: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let base = fs::canonicalize(root.path())?;
        let suite = parse_suite(include_str!("../inputs/minimal-suite.toml"))?;
        let store = Store::open(&base.join("evidence"))?;
        for directory in ["cache", "home", "config"] {
            fs::create_dir(base.join(directory))?;
        }
        let request = RunRequest {
            repository: base.clone(),
            candidate: ExecutableSource::Prebuilt(script(&base, "candidate", candidate)?),
            previous: Some(ExecutableSource::Prebuilt(script(
                &base, "previous", previous,
            )?)),
            reference: None,
            generator: Some(ExecutableSource::Prebuilt(script(
                &base,
                "generator",
                "for output do :; done\nprintf 'ABCD\\nEFGH\\n' > \"$output\"",
            )?)),
            git: None,
            tools: None,
            pipeline: None,
            cache_root: base.join("cache"),
            home: base.join("home"),
            config: base.join("config"),
        };
        let runner = ProcessRunner::new(ExecutionPolicy {
            timeout: Duration::from_secs(2),
            max_stream_bytes: 4096,
            cancellation: Arc::new(AtomicBool::new(false)),
        });
        Ok(Self {
            root,
            suite,
            request,
            store,
            runner,
        })
    }
    pub fn prepare(&self) -> Result<PreparedExperiment, BenchError> {
        prepare_experiment(
            &ExperimentPreparation {
                run: &self.request,
                suite: &self.suite,
                profile: MeasurementProfile::Full,
                selected_cases: &[],
                expected_datasets: None,
            },
            &self.store,
            &self.runner,
        )
    }
}
