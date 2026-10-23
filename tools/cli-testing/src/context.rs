use crate::{
    Error, Fixtures, Result,
    catalog::{Case, Execution, Input, Suite, relative_path},
};
use assert_fs::{
    TempDir,
    fixture::{ChildPath, PathChild},
};
use std::{
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    time::Duration,
};

/// Package-level helpers shared by catalog and manually orchestrated tests.
#[derive(Clone, Debug)]
pub struct TestContext {
    binary: PathBuf,
    package_dir: PathBuf,
}

impl TestContext {
    #[must_use]
    pub fn new(binary: impl Into<PathBuf>, package_dir: impl Into<PathBuf>) -> Self {
        Self {
            binary: binary.into(),
            package_dir: package_dir.into(),
        }
    }

    #[must_use]
    pub fn command(&self) -> assert_cmd::Command {
        crate::command(&self.binary, &self.package_dir)
    }

    #[must_use]
    pub fn std_command(&self) -> Command {
        crate::std_command(&self.binary, &self.package_dir)
    }

    #[must_use]
    pub fn fixtures(&self) -> Fixtures {
        Fixtures::new(self.package_dir.join("tests"))
    }

    /// Load the owning package's catalog.
    /// # Errors
    /// Reports invalid catalogs and unreadable fixtures.
    pub fn suite(&self) -> Result<Suite> {
        Suite::load(self.package_dir.join("tests/cases.toml"))
    }

    /// Resolve a recipe before executing any child process.
    /// # Errors
    /// Reports invalid catalogs, unknown names, and unreadable fixtures.
    pub fn case(&self, name: &str) -> Result<Context> {
        let suite = self.suite()?;
        Context::prepare(self.clone(), &suite, suite.case(name)?)
    }

    /// Create a temporary cwd. This isolates fixture mutations, not host access.
    /// # Errors
    /// Reports temporary directory creation failures.
    pub fn workspace(&self) -> Result<Workspace> {
        Ok(Workspace {
            directory: TempDir::new()
                .map_err(|error| Error::caused_by("cannot create test workspace", error))?,
            context: self.clone(),
        })
    }
}

/// Owned temporary files with package fixture access and configured commands.
#[derive(Debug)]
pub struct Workspace {
    directory: TempDir,
    context: TestContext,
}

impl Workspace {
    #[must_use]
    pub fn path(&self) -> &Path {
        self.directory.path()
    }

    #[must_use]
    pub fn child(&self, path: impl AsRef<Path>) -> ChildPath {
        self.directory.child(path)
    }

    #[must_use]
    pub fn command(&self) -> assert_cmd::Command {
        crate::command(&self.context.binary, self.path())
    }

    #[must_use]
    pub fn std_command(&self) -> Command {
        crate::std_command(&self.context.binary, self.path())
    }

    /// Copy one input file as raw bytes, creating destination parents.
    /// # Errors
    /// Rejects invalid relative names and reports fixture or write failures.
    pub fn copy_input(
        &self,
        source: impl AsRef<Path>,
        destination: impl AsRef<Path>,
    ) -> Result<PathBuf> {
        relative_path(destination.as_ref())?;
        let input = self.context.fixtures().read_input(source)?;
        let path = self.path().join(destination);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, input)?;
        Ok(path)
    }
}

enum PreparedInput {
    Closed,
    Pipe(Vec<u8>),
    File(fs::File),
}

/// A prepared case, with at most one candidate execution.
/// Changes are rejected once execution has been attempted, including on failure.
pub struct Context {
    name: String,
    execution: Execution,
    test: TestContext,
    command: Option<Command>,
    input: PreparedInput,
    expected_exit: i32,
    expected_stdout: Vec<u8>,
    expected_stderr: Vec<u8>,
    output: Option<Output>,
    attempted: bool,
    workspace: Option<Workspace>,
}

impl Context {
    #[must_use]
    pub fn fixtures(&self) -> Fixtures {
        self.test.fixtures()
    }

    pub(crate) fn prepare(test: TestContext, suite: &Suite, case: &Case) -> Result<Self> {
        let (mut stdout, stderr) = case.expect.bytes(suite)?;
        if let Some(adapt) = &case.adapt {
            stdout = adapt.expected_stdout.apply(&stdout);
        }
        let mut command = test.std_command();
        command.args(&case.args).envs(&suite.env).envs(&case.env);
        let input = prepare_input(suite, case.stdin.as_ref())?;
        Ok(Self {
            name: case.name.clone(),
            execution: case.execution,
            test,
            command: Some(command),
            input,
            expected_exit: case.expect.exit,
            expected_stdout: stdout,
            expected_stderr: stderr,
            output: None,
            attempted: false,
            workspace: None,
        })
    }

    fn ensure_ready(&self) -> Result {
        if self.attempted {
            Err(Error::new(format!(
                "case {:?} already executed; configuration is frozen",
                self.name
            )))
        } else {
            Ok(())
        }
    }

    fn command_mut(&mut self) -> Result<&mut Command> {
        self.ensure_ready()?;
        self.command
            .as_mut()
            .ok_or_else(|| Error::new("case command is unavailable"))
    }

    /// Append arguments before execution.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn args(&mut self, args: impl IntoIterator<Item = impl AsRef<OsStr>>) -> Result {
        self.command_mut()?.args(args);
        Ok(())
    }

    /// Override a child environment variable before execution.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn env(&mut self, key: impl AsRef<OsStr>, value: impl AsRef<OsStr>) -> Result {
        self.command_mut()?.env(key, value);
        Ok(())
    }

    /// Change the child cwd before execution.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn current_dir(&mut self, path: impl AsRef<Path>) -> Result {
        self.command_mut()?.current_dir(path);
        Ok(())
    }

    /// Replace the expected exit code before execution.
    /// # Errors
    /// Rejects codes outside 0..=255 and changes after execution.
    pub fn expect_exit(&mut self, code: i32) -> Result {
        self.ensure_ready()?;
        if !(0..=255).contains(&code) {
            return Err(Error::new("exit code must be between 0 and 255"));
        }
        self.expected_exit = code;
        Ok(())
    }

    /// Replace expected stderr without changing captured candidate bytes.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn expect_stderr(&mut self, bytes: impl Into<Vec<u8>>) -> Result {
        self.ensure_ready()?;
        self.expected_stderr = bytes.into();
        Ok(())
    }

    /// Replace stdin with piped bytes before execution.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn write_stdin(&mut self, bytes: impl Into<Vec<u8>>) -> Result {
        self.ensure_ready()?;
        self.input = PreparedInput::Pipe(bytes.into());
        Ok(())
    }

    /// Explicitly adapt expected stdout without changing captured candidate bytes.
    /// # Errors
    /// Fails after execution has been attempted.
    pub fn map_expected_stdout(&mut self, map: impl FnOnce(&[u8]) -> Vec<u8>) -> Result {
        self.ensure_ready()?;
        self.expected_stdout = map(&self.expected_stdout);
        Ok(())
    }

    /// Create and use an owned temporary cwd; subsequent calls reuse it.
    /// # Errors
    /// Fails when creating a workspace after execution, or if directory creation fails.
    pub fn workspace(&mut self) -> Result<&Workspace> {
        if self.workspace.is_none() {
            self.ensure_ready()?;
            let workspace = self.test.workspace()?;
            self.command_mut()?.current_dir(workspace.path());
            self.workspace = Some(workspace);
        }
        self.workspace
            .as_ref()
            .ok_or_else(|| Error::new("test workspace unavailable"))
    }

    /// Execute once, or return the already captured result.
    /// # Errors
    /// Reports spawn, timeout, and I/O failures. Failed attempts are never retried.
    pub fn run(&mut self) -> Result<&Output> {
        if !self.attempted {
            self.attempted = true;
            let mut command = self
                .command
                .take()
                .ok_or_else(|| Error::new("case command unavailable"))?;
            let input = std::mem::replace(&mut self.input, PreparedInput::Closed);
            let pipe = match input {
                PreparedInput::Closed => {
                    command.stdin(Stdio::null());
                    None
                }
                PreparedInput::File(file) => {
                    command.stdin(file);
                    None
                }
                PreparedInput::Pipe(bytes) => Some(bytes),
            };
            self.output = Some(capture(command, pipe).map_err(|error| {
                Error::caused_by(format!("cannot execute case {:?}", self.name), error)
            })?);
        }
        self.output
            .as_ref()
            .ok_or_else(|| Error::new(format!("case {:?} execution previously failed", self.name)))
    }

    /// Execute if necessary and check the declared exit code and both streams.
    /// # Errors
    /// Returns execution failures or byte/status mismatches with the case name.
    pub fn check(&mut self) -> Result {
        self.run()?;
        let output = self
            .output
            .as_ref()
            .ok_or_else(|| Error::new("missing case output"))?;
        check_status(&self.name, output, self.expected_exit)?;
        check_bytes(&self.name, "stdout", &output.stdout, &self.expected_stdout)?;
        check_bytes(&self.name, "stderr", &output.stderr, &self.expected_stderr)
    }
}

/// Run a Rust-owned recipe's customization, followed by its standard checks.
/// Used by `#[test_case]`; a failed body prevents subsequent automatic execution.
/// # Errors
/// Reports catalog, ownership, customization, execution, and assertion failures.
pub fn customize(
    test: &TestContext,
    name: &str,
    body: impl FnOnce(&mut Context) -> Result,
) -> Result {
    let mut context = test.case(name)?;
    if context.execution != Execution::Rust {
        return Err(Error::new(format!(
            "case {name:?} requires execution = \"rust\" when used by #[test_case]"
        )));
    }
    body(&mut context)?;
    context.check()
}

fn prepare_input(suite: &Suite, input: Option<&Input>) -> Result<PreparedInput> {
    match input {
        None => Ok(PreparedInput::Closed),
        Some(Input::File { path }) => Ok(PreparedInput::File(fs::File::open(
            suite.fixture_path(path)?,
        )?)),
        Some(input @ Input::Pipe { .. }) => Ok(PreparedInput::Pipe(input.bytes(suite)?)),
    }
}

pub fn capture(mut command: Command, stdin: Option<Vec<u8>>) -> Result<Output> {
    use std::io::{Read, Write};
    use wait_timeout::ChildExt;

    // assert_cmd always installs a pipe on stdin, including when wrapping a
    // standard Command. Keep this native path for explicit regular-file input.
    if stdin.is_some() {
        command.stdin(Stdio::piped());
    }
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Error::new("missing captured stdout"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| Error::new("missing captured stderr"))?;
    let read = |mut stream: Box<dyn Read + Send>| {
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            stream.read_to_end(&mut bytes).map(|_| bytes)
        })
    };
    let stdout = read(Box::new(stdout));
    let stderr = read(Box::new(stderr));
    let writer = stdin
        .zip(child.stdin.take())
        .map(|(bytes, mut pipe)| std::thread::spawn(move || pipe.write_all(&bytes)));
    let waited = child.wait_timeout(Duration::from_secs(30));
    if !matches!(waited, Ok(Some(_))) {
        let _ = child.kill();
        let _ = child.wait();
    }
    let stdout = stdout
        .join()
        .map_err(|_| Error::new("stdout reader panicked"))??;
    let stderr = stderr
        .join()
        .map_err(|_| Error::new("stderr reader panicked"))??;
    if let Some(writer) = writer
        && let Err(error) = writer
            .join()
            .map_err(|_| Error::new("stdin writer panicked"))?
        && error.kind() != std::io::ErrorKind::BrokenPipe
    {
        return Err(error.into());
    }
    let status = waited?.ok_or_else(|| Error::new("CLI exceeded its 30-second deadline"))?;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

pub fn check_status(name: &str, output: &Output, expected: i32) -> Result {
    if output.status.code() != Some(expected) {
        return Err(Error::new(format!(
            "case {name:?}: expected exit {expected}, got {}; stderr bytes: {:?}",
            output.status, output.stderr
        )));
    }
    Ok(())
}

pub fn check_bytes(name: &str, stream: &str, actual: &[u8], expected: &[u8]) -> Result {
    if actual == expected {
        return Ok(());
    }
    let offset = actual
        .iter()
        .zip(expected)
        .position(|(left, right)| left != right)
        .unwrap_or_else(|| actual.len().min(expected.len()));
    let actual_preview: Vec<_> = actual.iter().skip(offset).take(32).copied().collect();
    let expected_preview: Vec<_> = expected.iter().skip(offset).take(32).copied().collect();
    Err(Error::new(format!(
        "case {name:?}: {stream} differs at byte {offset}; expected {} bytes, got {}; expected next bytes {expected_preview:?}, actual {actual_preview:?}",
        expected.len(),
        actual.len()
    )))
}
