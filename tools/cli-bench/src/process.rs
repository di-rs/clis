use crate::{BenchError, Stream};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{Arc, atomic::AtomicBool},
    time::Duration,
};

/// Complete, isolated child context. All paths must be absolute UTF-8 paths.
#[derive(Clone, Debug)]
pub struct CommandSpec {
    pub program: PathBuf,
    pub argv: Vec<String>,
    pub cwd: PathBuf,
    pub environment: BTreeMap<String, String>,
    pub stdin: CommandInput,
    pub stdout: CommandOutput,
}
/// Resolved input; pipelines are explicit commands assembled by the invocation layer.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CommandInput {
    Null,
    File(PathBuf),
}
/// A real output boundary; correctness callers can choose Capture separately.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CommandOutput {
    Capture,
    Discard,
    DrainedPipe,
    File(PathBuf),
}
/// Fresh capture files; existing files are never overwritten.
#[derive(Clone, Debug)]
pub struct CapturePaths {
    pub stdout: PathBuf,
    pub stderr: PathBuf,
}
/// Caller-owned limits and cancellation. No process-global handler is installed.
#[derive(Clone, Debug)]
pub struct ExecutionPolicy {
    pub timeout: Duration,
    pub max_stream_bytes: u64,
    pub cancellation: Arc<AtomicBool>,
}
/// Native termination, with exit codes kept distinct from signals.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProcessStatus {
    Exit(i32),
    Signal(i32),
}
/// Why a bounded execution was stopped, even if the child subsequently exits zero.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StopReason {
    Timeout,
    Cancelled,
    OutputLimit(Stream),
}
/// Native status and bounded stream counts retained for successful and stopped runs.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcessOutcome {
    pub status: ProcessStatus,
    pub stopped: Option<StopReason>,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
}
impl ProcessOutcome {
    /// Require the exact declared status, never accepting a signal or a stopped run.
    ///
    /// # Errors
    /// Returns an execution failure for a mismatch or invalid expected status.
    pub fn check_expected(&self, expected: i32) -> Result<(), BenchError> {
        if !(0..=255).contains(&expected)
            || self.stopped.is_some()
            || self.status != ProcessStatus::Exit(expected)
        {
            return Err(BenchError::Execution(format!(
                "expected exit {expected}, observed {:?}, stopped: {:?}",
                self.status, self.stopped
            )));
        }
        Ok(())
    }
}
/// Owns only policy; each execute call owns and reaps its separate process group.
#[derive(Clone, Debug)]
pub struct ProcessRunner {
    policy: ExecutionPolicy,
}
impl ProcessRunner {
    #[must_use]
    pub const fn new(policy: ExecutionPolicy) -> Self {
        Self { policy }
    }
    /// Execute a command, capturing with fixed-size buffers and enforcing its limits.
    ///
    /// # Errors
    /// Returns invalid configuration, spawn, stream, or cleanup errors. Timeout,
    /// cancellation and quota stops are retained in the returned outcome.
    pub fn execute(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
    ) -> Result<ProcessOutcome, BenchError> {
        validate_command(spec, paths, &self.policy)?;
        if self.policy.cancellation.load(Ordering::Relaxed) {
            return Err(BenchError::Execution("cancelled before spawn".into()));
        }
        let mut command = Command::new(&spec.program);
        command
            .args(&spec.argv)
            .current_dir(&spec.cwd)
            .env_clear()
            .envs(&spec.environment)
            .process_group(0);
        command.stdin(match &spec.stdin {
            CommandInput::Null => Stdio::null(),
            CommandInput::File(path) => Stdio::from(open_input(path)?),
        });
        let output = match &spec.stdout {
            CommandOutput::Capture => Some(fresh_file(&paths.stdout)?),
            _ => None,
        };
        let stderr = fresh_file(&paths.stderr)?;
        command
            .stdout(match &spec.stdout {
                CommandOutput::Capture | CommandOutput::DrainedPipe => Stdio::piped(),
                CommandOutput::Discard => Stdio::null(),
                CommandOutput::File(path) => Stdio::from(fresh_file(path)?),
            })
            .stderr(Stdio::piped());
        let start = Instant::now();
        let mut owned = OwnedGroup::spawn(&mut command)?;
        let (sender, receiver) = mpsc::channel();
        let mut workers = Vec::new();
        let run = (|| {
            if let Some(stdout) = owned.child.stdout.take() {
                workers.push(start_drain(
                    stdout,
                    output,
                    Stream::Stdout,
                    self.policy.max_stream_bytes,
                    sender.clone(),
                )?);
            }
            let stderr_pipe = owned
                .child
                .stderr
                .take()
                .ok_or_else(|| BenchError::Execution("missing stderr pipe".into()))?;
            workers.push(start_drain(
                stderr_pipe,
                Some(stderr),
                Stream::Stderr,
                self.policy.max_stream_bytes,
                sender,
            )?);
            monitor(&mut owned, &receiver, workers.len(), start, &self.policy)
        })();
        // Cleanup runs on monitor/reader/thread-creation errors too; the original error wins.
        let cleanup = owned.finish();
        drop(owned);
        let mut join_error = None;
        for worker in workers {
            if worker.join().is_err() {
                join_error = Some(BenchError::Execution("capture worker panicked".into()));
            }
        }
        let mut outcome = run?;
        outcome.status = native_status(cleanup?)?;
        if let Some(error) = join_error {
            return Err(error);
        }
        // Readers interrupted by cleanup may still have their final counts to report.
        for (stream, result) in receiver.try_iter() {
            let capture = result?;
            set_bytes(&mut outcome, stream, capture.bytes);
            if capture.exceeded && outcome.stopped.is_none() {
                outcome.stopped = Some(StopReason::OutputLimit(stream));
            }
        }
        Ok(outcome)
    }
}

use rustix::process::{Pid, Signal, kill_process_group, test_kill_process_group};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::unix::process::{CommandExt, ExitStatusExt},
    path::Path,
    process::{Child, Command, ExitStatus, Stdio},
    sync::{atomic::Ordering, mpsc},
    thread::{self, JoinHandle},
    time::Instant,
};

const POLL: Duration = Duration::from_millis(5);
const GRACE: Duration = Duration::from_secs(1);

pub fn utf8_path(path: &Path) -> Result<&str, BenchError> {
    let value = path
        .to_str()
        .ok_or_else(|| BenchError::invalid("invocation paths must be UTF-8"))?;
    if !path.is_absolute() || value.contains('\0') {
        return Err(BenchError::invalid(
            "invocation paths must be absolute and NUL-free",
        ));
    }
    Ok(value)
}
fn validate_command(
    spec: &CommandSpec,
    paths: &CapturePaths,
    policy: &ExecutionPolicy,
) -> Result<(), BenchError> {
    if policy.timeout.is_zero() || policy.max_stream_bytes == 0 {
        return Err(BenchError::invalid("execution limits must be positive"));
    }
    for path in [&spec.program, &spec.cwd, &paths.stdout, &paths.stderr] {
        utf8_path(path)?;
    }
    if spec.argv.iter().any(|arg| arg.contains('\0'))
        || spec
            .environment
            .iter()
            .any(|(key, value)| key.is_empty() || key.contains(['=', '\0']) || value.contains('\0'))
    {
        return Err(BenchError::invalid("invalid argv or child environment"));
    }
    if let CommandInput::File(path) = &spec.stdin {
        utf8_path(path)?;
    }
    if let CommandOutput::File(path) = &spec.stdout {
        utf8_path(path)?;
    }
    Ok(())
}
fn fresh_file(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}
fn open_input(path: &Path) -> Result<File, BenchError> {
    if !std::fs::symlink_metadata(path)?.is_file() {
        return Err(BenchError::invalid(
            "stdin requires a regular, non-symlink file",
        ));
    }
    Ok(File::open(path)?)
}
fn native_status(status: ExitStatus) -> Result<ProcessStatus, BenchError> {
    if let Some(code) = status.code() {
        return Ok(ProcessStatus::Exit(code));
    }
    status
        .signal()
        .map(ProcessStatus::Signal)
        .ok_or_else(|| BenchError::Execution("unavailable native child status".into()))
}

// Constructed only from a successful process_group(0) spawn, never from evidence PIDs.
struct OwnedGroup {
    child: Child,
    pgid: Pid,
    status: Option<ExitStatus>,
    finished: bool,
}
impl OwnedGroup {
    fn spawn(command: &mut Command) -> Result<Self, BenchError> {
        let mut child = command.spawn()?;
        let pgid = i32::try_from(child.id())
            .ok()
            .filter(|pid| *pid > 1)
            .and_then(Pid::from_raw);
        if let Some(pgid) = pgid {
            return Ok(Self {
                child,
                pgid,
                status: None,
                finished: false,
            });
        }
        let _ = child.kill();
        let _ = child.wait();
        Err(BenchError::Execution(
            "spawn did not supply an owned PGID greater than 1".into(),
        ))
    }
    fn poll(&mut self) -> std::io::Result<()> {
        if self.status.is_none() {
            self.status = self.child.try_wait()?;
        }
        Ok(())
    }
    fn signal(&self, signal: Signal) -> std::io::Result<()> {
        match kill_process_group(self.pgid, signal) {
            Ok(()) | Err(rustix::io::Errno::SRCH) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
    fn finish(&mut self) -> Result<ExitStatus, BenchError> {
        let start = Instant::now();
        self.poll()?;
        let term_error = match self.signal(Signal::TERM) {
            Ok(()) => None,
            Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => Some(error),
            Err(error) => return Err(error.into()),
        };
        loop {
            self.poll()?;
            // macOS can deny signals while exit is in progress, before wait can
            // reap. Reconcile only inside this same one-second grace period.
            let probe_error = match test_kill_process_group(self.pgid) {
                Err(rustix::io::Errno::SRCH) => break,
                Ok(()) => None,
                Err(rustix::io::Errno::PERM) => Some(std::io::Error::from(rustix::io::Errno::PERM)),
                Err(error) => return Err(std::io::Error::from(error).into()),
            };
            if start.elapsed() >= GRACE {
                let kill_result = self.signal(Signal::KILL);
                if let Some(error) = term_error.or(probe_error) {
                    return Err(error.into());
                }
                kill_result?;
                break;
            }
            thread::sleep(POLL);
        }
        let status = match self.status {
            Some(status) => status,
            None => self.child.wait()?,
        };
        self.finished = true;
        Ok(status)
    }
}
impl Drop for OwnedGroup {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.signal(Signal::KILL);
            let _ = self.child.wait();
        }
    }
}
struct CaptureResult {
    bytes: u64,
    exceeded: bool,
}
type CaptureMessage = (Stream, std::io::Result<CaptureResult>);
fn start_drain(
    reader: impl Read + Send + 'static,
    output: Option<File>,
    stream: Stream,
    limit: u64,
    sender: mpsc::Sender<CaptureMessage>,
) -> std::io::Result<JoinHandle<()>> {
    thread::Builder::new()
        .name(format!("capture-{stream:?}"))
        .spawn(move || {
            let result = drain(reader, output, limit);
            let _ = sender.send((stream, result));
        })
}
fn drain(
    mut reader: impl Read,
    mut output: Option<File>,
    limit: u64,
) -> std::io::Result<CaptureResult> {
    let mut bytes = 0_u64;
    let mut buffer = [0_u8; 8192];
    loop {
        let count = match reader.read(&mut buffer) {
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            result => result?,
        };
        if count == 0 {
            if let Some(file) = &mut output {
                file.flush()?;
            }
            return Ok(CaptureResult {
                bytes,
                exceeded: false,
            });
        }
        let remaining = limit.saturating_sub(bytes);
        let kept = count.min(usize::try_from(remaining).unwrap_or(usize::MAX));
        if let Some(file) = &mut output {
            file.write_all(
                buffer
                    .get(..kept)
                    .ok_or_else(|| std::io::Error::other("invalid read size"))?,
            )?;
        }
        bytes = bytes.saturating_add(u64::try_from(kept).unwrap_or(u64::MAX));
        if kept < count {
            if let Some(file) = &mut output {
                file.flush()?;
            }
            return Ok(CaptureResult {
                bytes,
                exceeded: true,
            });
        }
    }
}
const fn set_bytes(outcome: &mut ProcessOutcome, stream: Stream, bytes: u64) {
    match stream {
        Stream::Stdout => outcome.stdout_bytes = bytes,
        Stream::Stderr => outcome.stderr_bytes = bytes,
    }
}
fn monitor(
    owned: &mut OwnedGroup,
    receiver: &mpsc::Receiver<CaptureMessage>,
    mut pending: usize,
    start: Instant,
    policy: &ExecutionPolicy,
) -> Result<ProcessOutcome, BenchError> {
    let mut outcome = ProcessOutcome {
        status: ProcessStatus::Exit(0),
        stopped: None,
        stdout_bytes: 0,
        stderr_bytes: 0,
    };
    loop {
        for (stream, result) in receiver.try_iter() {
            pending = pending.saturating_sub(1);
            let capture = result?;
            set_bytes(&mut outcome, stream, capture.bytes);
            if capture.exceeded && outcome.stopped.is_none() {
                outcome.stopped = Some(StopReason::OutputLimit(stream));
            }
        }
        if outcome.stopped.is_some() {
            break;
        }
        if policy.cancellation.load(Ordering::Relaxed) {
            outcome.stopped = Some(StopReason::Cancelled);
            break;
        }
        if start.elapsed() >= policy.timeout {
            outcome.stopped = Some(StopReason::Timeout);
            break;
        }
        owned.poll()?;
        if owned.status.is_some() && pending == 0 {
            break;
        }
        thread::sleep(POLL);
    }
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::ffi::OsStringExt;
    #[test]
    fn exact_status_distinguishes_zero_nonzero_signal_and_stop() {
        let mut result = ProcessOutcome {
            status: ProcessStatus::Exit(1),
            stopped: None,
            stdout_bytes: 0,
            stderr_bytes: 0,
        };
        assert!(result.check_expected(1).is_ok());
        assert!(result.check_expected(0).is_err());
        result.status = ProcessStatus::Exit(0);
        assert!(result.check_expected(1).is_err());
        result.status = ProcessStatus::Signal(1);
        assert!(result.check_expected(1).is_err());
        result.status = ProcessStatus::Exit(0);
        result.stopped = Some(StopReason::Timeout);
        assert!(result.check_expected(0).is_err());
        assert!(result.check_expected(256).is_err());
    }
    fn inputs() -> (CommandSpec, CapturePaths, ExecutionPolicy) {
        (
            CommandSpec {
                program: "/fixture".into(),
                argv: vec![],
                cwd: "/scratch".into(),
                environment: BTreeMap::new(),
                stdin: CommandInput::Null,
                stdout: CommandOutput::Capture,
            },
            CapturePaths {
                stdout: "/stdout".into(),
                stderr: "/stderr".into(),
            },
            ExecutionPolicy {
                timeout: Duration::from_secs(1),
                max_stream_bytes: 1,
                cancellation: Arc::new(AtomicBool::new(false)),
            },
        )
    }
    #[test]
    fn invalid_limits_and_context_fail_before_spawn() {
        let (mut spec, paths, mut policy) = inputs();
        policy.timeout = Duration::ZERO;
        assert!(validate_command(&spec, &paths, &policy).is_err());
        policy.timeout = Duration::from_secs(1);
        policy.max_stream_bytes = 0;
        assert!(validate_command(&spec, &paths, &policy).is_err());
        policy.max_stream_bytes = 1;
        spec.argv.push("nul\0argument".into());
        assert!(validate_command(&spec, &paths, &policy).is_err());
        spec.argv.clear();
        spec.environment.insert("bad=key".into(), "value".into());
        assert!(validate_command(&spec, &paths, &policy).is_err());
        spec.environment.clear();
        spec.program = "relative".into();
        assert!(validate_command(&spec, &paths, &policy).is_err());
        spec.program = std::ffi::OsString::from_vec(vec![b'/', 255]).into();
        assert!(validate_command(&spec, &paths, &policy).is_err());
    }
    #[test]
    fn pre_cancelled_execution_never_opens_capture_files() {
        let (spec, paths, policy) = inputs();
        policy.cancellation.store(true, Ordering::Relaxed);
        let result = ProcessRunner::new(policy).execute(&spec, &paths);
        assert!(matches!(result, Err(BenchError::Execution(_))));
    }
}
