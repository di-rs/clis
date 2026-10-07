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
/// Logical size bound for a child-created regular file. Polling permits overshoot.
#[derive(Clone, Debug)]
pub struct OutputFileLimit {
    pub path: PathBuf,
    pub max_bytes: u64,
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
    FileLimit,
    EvidenceLimit,
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
/// Owns only policy; each execute call controls its separate process group.
/// Unconfirmed termination returns failure; a private waiter may reap later.
#[derive(Clone, Debug)]
pub struct ProcessRunner {
    policy: ExecutionPolicy,
    evidence: Option<crate::budget::EvidenceBudget>,
}
impl ProcessRunner {
    #[must_use]
    pub const fn new(policy: ExecutionPolicy) -> Self {
        Self {
            policy,
            evidence: None,
        }
    }
    pub(crate) fn bounded(&self, timeout: Duration, max_stream_bytes: u64) -> Self {
        let mut policy = self.policy.clone();
        policy.timeout = policy.timeout.min(timeout);
        policy.max_stream_bytes = policy.max_stream_bytes.min(max_stream_bytes);
        Self {
            policy,
            evidence: self.evidence.clone(),
        }
    }
    pub(crate) fn with_evidence(&self, budget: &crate::budget::EvidenceBudget) -> Self {
        Self {
            policy: self.policy.clone(),
            evidence: Some(budget.clone()),
        }
    }
    pub(crate) fn with_default_evidence(&self, budget: &crate::budget::EvidenceBudget) -> Self {
        if self.evidence.is_some() {
            self.clone()
        } else {
            self.with_evidence(budget)
        }
    }
    pub(crate) fn write_json<T: serde::Serialize>(
        &self,
        path: &Path,
        value: &T,
    ) -> Result<(), BenchError> {
        let bytes = serde_json::to_vec_pretty(value)?;
        if let Some(budget) = &self.evidence
            && budget.contains(path)
            && let Err(error) = budget.reserve_write(bytes.len())
        {
            if budget.is_failed() {
                return Err(error);
            }
            // Keep the observation that could not fit as bounded failure metadata,
            // then propagate quota failure to stop the stage.
            budget.fail();
            budget.reserve_write(bytes.len())?;
            crate::store::atomic_write(path, &bytes)?;
            return Err(error);
        }
        crate::store::atomic_write(path, &bytes)
    }
    pub(crate) fn write_evidence(&self, path: &Path, bytes: &[u8]) -> Result<(), BenchError> {
        if let Some(budget) = &self.evidence
            && budget.contains(path)
        {
            budget.reserve_write(bytes.len())?;
        }
        crate::store::atomic_write(path, bytes)
    }
    /// Execute a command, capturing with fixed-size buffers and enforcing its limits.
    ///
    /// # Errors
    /// Returns invalid configuration, spawn, stream, or cleanup errors. Timeout,
    /// cancellation and quota stops are retained in the returned outcome. Cleanup
    /// allows one second after TERM and one second after KILL for confirmation.
    /// Persistent failures stop/join captures and return error; a live direct child
    /// is handed to a private waiter until exit, with no capture files attached.
    pub fn execute(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
    ) -> Result<ProcessOutcome, BenchError> {
        self.execute_controlled(spec, paths, &NativeControl, None)
    }
    /// Execute with an additional monitored child-created output file limit.
    ///
    /// # Errors
    /// Like `execute`, plus invalid file targets and monitored file failures.
    pub fn execute_with_file_limit(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
        limit: &OutputFileLimit,
    ) -> Result<ProcessOutcome, BenchError> {
        utf8_path(&limit.path)?;
        match std::fs::symlink_metadata(&limit.path) {
            Ok(_) => return Err(BenchError::invalid("monitored output must be a fresh path")),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        self.execute_controlled(spec, paths, &NativeControl, Some(limit))
    }
    fn evidence_monitor(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
        file_limit: Option<&OutputFileLimit>,
    ) -> Result<EvidenceMonitor, BenchError> {
        // Inventory is outside the child interval; during execution only explicitly
        // bound external files are polled, never the whole run directory.
        let mut external = Vec::new();
        let allowance = if let Some(budget) = &self.evidence {
            if let Some(limit) = file_limit
                && budget.contains(&limit.path)
            {
                external.push((limit.path.clone(), 0));
            }
            if let CommandOutput::File(path) = &spec.stdout
                && budget.contains(path)
                && !external.iter().any(|(existing, _)| existing == path)
            {
                external.push((path.clone(), 0));
            }
            if budget.contains(&paths.stdout)
                || budget.contains(&paths.stderr)
                || !external.is_empty()
            {
                let remaining = budget.remaining()?;
                if remaining == 0 {
                    budget.fail();
                    return Err(BenchError::Evidence(
                        "run evidence budget exhausted before child".into(),
                    ));
                }
                Some(Arc::new(crate::budget::CaptureAllowance::new(remaining)))
            } else {
                None
            }
        } else {
            None
        };
        Ok(EvidenceMonitor {
            allowance,
            external,
        })
    }
    fn execute_controlled(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
        control: &impl GroupControl,
        file_limit: Option<&OutputFileLimit>,
    ) -> Result<ProcessOutcome, BenchError> {
        validate_command(spec, paths, &self.policy)?;
        if self.policy.cancellation.load(Ordering::Relaxed) {
            return Err(BenchError::Execution("cancelled before spawn".into()));
        }
        let mut evidence = self.evidence_monitor(spec, paths, file_limit)?;
        let allowance = evidence.allowance.clone();
        let (mut command, output, stderr) = command_with_captures(spec, paths)?;
        let start = Instant::now();
        let mut owned = OwnedGroup::spawn(&mut command, control)?;
        let (sender, receiver) = mpsc::channel();
        let mut workers = Vec::new();
        let stop_capture = Arc::new(AtomicBool::new(false));
        let run = (|| {
            if let Some(stdout) = owned.child_mut()?.stdout.take() {
                workers.push(start_drain(
                    stdout,
                    output,
                    Stream::Stdout,
                    self.policy.max_stream_bytes,
                    sender.clone(),
                    Arc::clone(&stop_capture),
                    allowance.clone().filter(|_| {
                        self.evidence
                            .as_ref()
                            .is_some_and(|budget| budget.contains(&paths.stdout))
                    }),
                )?);
            }
            let stderr_pipe = owned
                .child_mut()?
                .stderr
                .take()
                .ok_or_else(|| BenchError::Execution("missing stderr pipe".into()))?;
            workers.push(start_drain(
                stderr_pipe,
                Some(stderr),
                Stream::Stderr,
                self.policy.max_stream_bytes,
                sender,
                Arc::clone(&stop_capture),
                allowance.clone().filter(|_| {
                    self.evidence
                        .as_ref()
                        .is_some_and(|budget| budget.contains(&paths.stderr))
                }),
            )?);
            monitor(
                &mut owned,
                &receiver,
                workers.len(),
                start,
                &self.policy,
                file_limit,
                &mut evidence,
            )
        })();
        // Cleanup runs on monitor/reader/thread-creation errors too; the original error wins.
        let cleanup = owned.finish();
        stop_capture.store(true, Ordering::Relaxed);
        let retire = owned.retire();
        drop(owned);
        let mut join_error = None;
        for worker in workers {
            if worker.join().is_err() {
                join_error = Some(BenchError::Execution("capture worker panicked".into()));
            }
        }
        let primary = run.and_then(|mut outcome| {
            outcome.status = native_status(cleanup?)?;
            Ok(outcome)
        });
        let mut outcome = match (primary, retire) {
            (result, Ok(())) => result?,
            (Err(primary), Err(reaper)) => return Err(with_reaper_error(primary, reaper)),
            (Ok(_), Err(reaper)) => return Err(reaper.into()),
        };
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
        if evidence.exceeded()? {
            outcome.stopped = Some(StopReason::EvidenceLimit);
        }
        if file_limit.map(file_exceeded).transpose()?.unwrap_or(false) && outcome.stopped.is_none()
        {
            outcome.stopped = Some(StopReason::FileLimit);
        }
        if outcome.stopped == Some(StopReason::EvidenceLimit)
            && let Some(budget) = &self.evidence
        {
            // Further children are forbidden; only bounded failure metadata may follow.
            budget.fail();
        }
        Ok(outcome)
    }
}

fn command_with_captures(
    spec: &CommandSpec,
    paths: &CapturePaths,
) -> Result<(Command, Option<File>, File), BenchError> {
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
    Ok((command, output, stderr))
}

fn file_exceeded(limit: &OutputFileLimit) -> Result<bool, BenchError> {
    match std::fs::symlink_metadata(&limit.path) {
        Ok(metadata) if metadata.is_file() => Ok(metadata.len() > limit.max_bytes),
        Ok(_) => Err(BenchError::Execution(
            "monitored output is not a regular file".into(),
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}

use rustix::process::{Pid, Signal, kill_process_group, test_kill_process_group};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::AsFd,
        unix::process::{CommandExt, ExitStatusExt},
    },
    path::Path,
    process::{Child, Command, ExitStatus, Stdio},
    sync::{atomic::Ordering, mpsc},
    thread::{self, JoinHandle},
    time::Instant,
};

const POLL: Duration = Duration::from_millis(5);
const GRACE: Duration = Duration::from_secs(1);
const KILL_CONFIRMATION: Duration = Duration::from_secs(1);

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
trait GroupControl {
    fn signal(&self, pgid: Pid, signal: Signal) -> rustix::io::Result<()>;
    fn probe(&self, pgid: Pid) -> rustix::io::Result<()>;
}
struct NativeControl;
impl GroupControl for NativeControl {
    fn signal(&self, pgid: Pid, signal: Signal) -> rustix::io::Result<()> {
        kill_process_group(pgid, signal)
    }
    fn probe(&self, pgid: Pid) -> rustix::io::Result<()> {
        test_kill_process_group(pgid)
    }
}
struct OwnedGroup<'a, C: GroupControl> {
    control: &'a C,
    child: Option<Child>,
    pgid: Pid,
    status: Option<ExitStatus>,
}
impl<'a, C: GroupControl> OwnedGroup<'a, C> {
    fn spawn(command: &mut Command, control: &'a C) -> Result<Self, BenchError> {
        let mut child = command.spawn()?;
        let pgid = i32::try_from(child.id())
            .ok()
            .filter(|pid| *pid > 1)
            .and_then(Pid::from_raw);
        if let Some(pgid) = pgid {
            return Ok(Self {
                control,
                child: Some(child),
                pgid,
                status: None,
            });
        }
        let _ = child.kill();
        defer_reap(child)?;
        Err(BenchError::Execution(
            "spawn did not supply an owned PGID greater than 1".into(),
        ))
    }
    fn child_mut(&mut self) -> std::io::Result<&mut Child> {
        self.child
            .as_mut()
            .ok_or_else(|| std::io::Error::other("child already retired"))
    }
    fn poll(&mut self) -> std::io::Result<()> {
        if self.status.is_none() {
            self.status = self.child_mut()?.try_wait()?;
        }
        Ok(())
    }
    fn retire(&mut self) -> std::io::Result<()> {
        if let Some(child) = self.child.take()
            && self.status.is_none()
        {
            return defer_reap(child);
        }
        Ok(())
    }
    fn signal(&self, signal: Signal) -> std::io::Result<()> {
        match self.control.signal(self.pgid, signal) {
            Ok(()) | Err(rustix::io::Errno::SRCH) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
    fn finish(&mut self) -> Result<ExitStatus, BenchError> {
        let start = Instant::now();
        self.poll()?;
        let mut primary = match self.signal(Signal::TERM) {
            Ok(()) => None,
            Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => Some(error),
            Err(error) => return Err(error.into()),
        };
        let mut killed_at = None;
        loop {
            if let Err(error) = self.poll() {
                return Err(primary.unwrap_or(error).into());
            }
            // Only confirmed group absence plus a reaped direct child clears a
            // transient macOS exit error. Neither polling phase blocks on wait.
            match self.control.probe(self.pgid) {
                Err(rustix::io::Errno::SRCH) => {
                    if let Some(status) = self.status {
                        return Ok(status);
                    }
                }
                Ok(()) => {}
                Err(rustix::io::Errno::PERM) => {
                    primary.get_or_insert_with(|| rustix::io::Errno::PERM.into());
                }
                Err(error) => return Err(primary.unwrap_or_else(|| error.into()).into()),
            }
            if let Some(killed) = killed_at {
                if Instant::now().saturating_duration_since(killed) >= KILL_CONFIRMATION {
                    return Err(primary
                        .unwrap_or_else(|| {
                            std::io::Error::new(
                                std::io::ErrorKind::TimedOut,
                                "owned process-group termination could not be confirmed",
                            )
                        })
                        .into());
                }
            } else if start.elapsed() >= GRACE {
                if let Err(error) = self.signal(Signal::KILL) {
                    if error.kind() != std::io::ErrorKind::PermissionDenied {
                        return Err(primary.unwrap_or(error).into());
                    }
                    primary.get_or_insert(error);
                }
                killed_at = Some(Instant::now());
            }
            thread::sleep(POLL);
        }
    }
}
impl<C: GroupControl> Drop for OwnedGroup<'_, C> {
    fn drop(&mut self) {
        if self.child.is_some() {
            // Unwinding fallback only: normal execution reports retire failures.
            let _ = self.signal(Signal::KILL);
            let _ = self.poll();
            let _ = self.retire();
        }
    }
}
fn defer_reap(mut child: Child) -> std::io::Result<()> {
    if child.try_wait()?.is_some() {
        return Ok(());
    }
    // Never retain capture pipes/files in the waiter, including a spawn error
    // before the capture workers were created.
    drop(child.stdin.take());
    drop(child.stdout.take());
    drop(child.stderr.take());
    thread::Builder::new()
        .name("cli-bench-reaper".into())
        .stack_size(128 * 1024)
        .spawn(move || {
            let _ = child.wait();
        })
        .map(drop)
}
#[derive(Debug, thiserror::Error)]
#[error("{primary}; child waiter unavailable: {reaper}")]
struct ReaperFailure {
    #[source]
    primary: BenchError,
    reaper: std::io::Error,
}
fn with_reaper_error(primary: BenchError, reaper: std::io::Error) -> BenchError {
    let kind = match &primary {
        BenchError::Io(error) => error.kind(),
        _ => std::io::ErrorKind::Other,
    };
    std::io::Error::new(kind, ReaperFailure { primary, reaper }).into()
}

struct CaptureResult {
    bytes: u64,
    exceeded: bool,
}
type CaptureMessage = (Stream, std::io::Result<CaptureResult>);
fn start_drain(
    reader: impl Read + AsFd + Send + 'static,
    output: Option<File>,
    stream: Stream,
    limit: u64,
    sender: mpsc::Sender<CaptureMessage>,
    stop: Arc<AtomicBool>,
    allowance: Option<Arc<crate::budget::CaptureAllowance>>,
) -> std::io::Result<JoinHandle<()>> {
    thread::Builder::new()
        .name(format!("capture-{stream:?}"))
        .spawn(move || {
            let result = drain(reader, output, limit, &stop, allowance.as_deref());
            let _ = sender.send((stream, result));
        })
}
fn drain(
    mut reader: impl Read + AsFd,
    mut output: Option<File>,
    limit: u64,
    stop: &AtomicBool,
    allowance: Option<&crate::budget::CaptureAllowance>,
) -> std::io::Result<CaptureResult> {
    let mut bytes = 0_u64;
    let mut buffer = [0_u8; 8192];
    let timeout = rustix::event::Timespec::try_from(POLL).map_err(std::io::Error::other)?;
    loop {
        if stop.load(Ordering::Relaxed) {
            if let Some(file) = &mut output {
                file.flush()?;
            }
            return Ok(CaptureResult {
                bytes,
                exceeded: false,
            });
        }
        // This worker is the sole reader of its pipe. A ready/HUP descriptor
        // permits one read without waiting for more bytes from surviving writers.
        let mut fds = [rustix::event::PollFd::new(
            &reader,
            rustix::event::PollFlags::IN,
        )];
        match rustix::event::poll(&mut fds, Some(&timeout)) {
            Ok(0) | Err(rustix::io::Errno::INTR) => continue,
            Ok(_) => {}
            Err(error) => return Err(error.into()),
        }
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
        let mut kept = count.min(usize::try_from(remaining).unwrap_or(usize::MAX));
        if output.is_some()
            && let Some(allowance) = allowance
        {
            kept = usize::try_from(allowance.claim(u64::try_from(kept).unwrap_or(u64::MAX)))
                .unwrap_or(0);
        }
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
struct EvidenceMonitor {
    allowance: Option<Arc<crate::budget::CaptureAllowance>>,
    external: Vec<(PathBuf, u64)>,
}
impl EvidenceMonitor {
    fn exceeded(&mut self) -> Result<bool, BenchError> {
        let Some(allowance) = &self.allowance else {
            return Ok(false);
        };
        for (path, previous) in &mut self.external {
            let bytes = match std::fs::symlink_metadata(path) {
                Ok(meta) if meta.is_file() => meta.len(),
                Ok(_) => {
                    return Err(BenchError::Evidence(
                        "non-regular monitored evidence".into(),
                    ));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => 0,
                Err(error) => return Err(error.into()),
            };
            allowance.claim(bytes.saturating_sub(*previous));
            *previous = bytes;
        }
        Ok(allowance.exceeded.load(Ordering::Relaxed))
    }
}
fn monitor(
    owned: &mut OwnedGroup<'_, impl GroupControl>,
    receiver: &mpsc::Receiver<CaptureMessage>,
    mut pending: usize,
    start: Instant,
    policy: &ExecutionPolicy,
    file_limit: Option<&OutputFileLimit>,
    evidence: &mut EvidenceMonitor,
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
        if evidence.exceeded()? {
            outcome.stopped = Some(StopReason::EvidenceLimit);
        }
        if file_limit.map(file_exceeded).transpose()?.unwrap_or(false) && outcome.stopped.is_none()
        {
            outcome.stopped = Some(StopReason::FileLimit);
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
    fn evidence_streams_share_capacity_and_every_child_rechecks_remaining_bytes()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let (mut spec, _, mut policy) = inputs();
        spec.program = "/bin/sh".into();
        spec.cwd = root.path().into();
        spec.argv = vec!["-c".into(), "printf 123456789; printf x >&2".into()];
        policy.max_stream_bytes = 100;
        let budget = crate::budget::EvidenceBudget::new(root.path().into(), 15);
        let runner = ProcessRunner::new(policy)
            .with_evidence(&budget)
            .bounded(Duration::from_secs(1), 100);
        let paths = CapturePaths {
            stdout: root.join("out"),
            stderr: root.join("err"),
        };
        runner.execute(&spec, &paths)?.check_expected(0)?;
        crate::test_support::equal(&crate::budget::size(root.path())?, &10)?;
        let second = CapturePaths {
            stdout: root.join("out2"),
            stderr: root.join("err2"),
        };
        let outcome = runner.execute(&spec, &second)?;
        crate::test_support::equal(&outcome.stopped, &Some(StopReason::EvidenceLimit))?;
        crate::test_support::equal(&crate::budget::size(root.path())?, &15)?;
        let third = CapturePaths {
            stdout: root.join("out3"),
            stderr: root.join("err3"),
        };
        crate::test_support::require(
            runner.execute(&spec, &third).is_err(),
            "started child at cap",
        )?;
        crate::test_support::require(!third.stderr.exists(), "created capture after cap")?;
        Ok(())
    }
    #[test]
    fn monitored_external_evidence_overshoot_is_retained_and_stops_later_children()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let (mut spec, _, mut policy) = inputs();
        spec.program = "/bin/sh".into();
        spec.cwd = root.path().into();
        spec.argv = vec!["-c".into(), "printf 12345678901234567890 > external".into()];
        policy.max_stream_bytes = 100;
        let budget = crate::budget::EvidenceBudget::new(root.path().into(), 10);
        let runner = ProcessRunner::new(policy).with_evidence(&budget);
        let paths = CapturePaths {
            stdout: root.join("out"),
            stderr: root.join("err"),
        };
        let outcome = runner.execute_with_file_limit(
            &spec,
            &paths,
            &OutputFileLimit {
                path: root.join("external"),
                max_bytes: 100,
            },
        )?;
        crate::test_support::equal(&outcome.stopped, &Some(StopReason::EvidenceLimit))?;
        crate::test_support::equal(&std::fs::metadata(root.join("external"))?.len(), &20)?;
        crate::test_support::require(budget.remaining().is_err(), "overshoot accepted new work")?;
        Ok(())
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
    struct DeniedControl {
        owned: mpsc::Sender<Pid>,
    }
    impl GroupControl for DeniedControl {
        fn signal(&self, pgid: Pid, _signal: Signal) -> rustix::io::Result<()> {
            let _ = self.owned.send(pgid);
            Err(rustix::io::Errno::PERM)
        }
        fn probe(&self, _pgid: Pid) -> rustix::io::Result<()> {
            Err(rustix::io::Errno::PERM)
        }
    }
    #[test]
    fn persistent_cleanup_error_returns_before_test_owned_cleanup()
    -> Result<(), Box<dyn std::error::Error>> {
        persistent_cleanup_case(false)
    }
    #[test]
    fn surviving_descendant_pipe_does_not_block_cleanup_error()
    -> Result<(), Box<dyn std::error::Error>> {
        persistent_cleanup_case(true)
    }
    struct TestOwnedGroup(Pid);
    impl Drop for TestOwnedGroup {
        fn drop(&mut self) {
            let _ = kill_process_group(self.0, Signal::KILL);
        }
    }
    fn persistent_cleanup_case(
        descendant_holds_pipe: bool,
    ) -> Result<(), Box<dyn std::error::Error>> {
        // The test retains the actual spawn-derived group, so RED is finite.
        let root = assert_fs::TempDir::new()?;
        let (mut spec, mut paths, mut policy) = inputs();
        spec.program = "/bin/sh".into();
        spec.cwd = root.path().into();
        spec.argv = vec![
            "-c".into(),
            if descendant_holds_pipe {
                "printf preserved; /bin/sleep 30 & echo $! > descendant.pid; exit 0".into()
            } else {
                "printf preserved; exec /bin/sleep 30".into()
            },
        ];
        paths.stdout = root.join("stdout");
        paths.stderr = root.join("stderr");
        policy.timeout = Duration::from_millis(150);
        policy.max_stream_bytes = 1024;
        let (groups, group_receiver) = mpsc::channel();
        let (completed, completion_receiver) = mpsc::channel();
        let observed_paths = paths.clone();
        let worker = thread::spawn(move || {
            let result = ProcessRunner::new(policy).execute_controlled(
                &spec,
                &paths,
                &DeniedControl { owned: groups },
                None,
            );
            let _ = completed.send(result);
        });
        let group = TestOwnedGroup(group_receiver.recv_timeout(Duration::from_secs(2))?);
        let direct_pid = group.0;
        let descendant_pid = if descendant_holds_pipe {
            let raw = std::fs::read_to_string(root.join("descendant.pid"))?
                .trim()
                .parse::<i32>()?;
            if raw <= 1 {
                return Err("invalid fixture descendant pid".into());
            }
            Pid::from_raw(raw)
        } else {
            None
        };
        let returned = completion_receiver.recv_timeout(Duration::from_secs(3));
        let snapshot = std::fs::read(&observed_paths.stdout);
        // Cleanup is independent of the deliberately failing production control.
        drop(group);
        worker.join().map_err(|_| "execution thread panicked")?;
        for pid in std::iter::once(direct_pid).chain(descendant_pid) {
            wait_for_fixture_exit(pid)?;
        }
        if let Ok(Err(BenchError::Io(error))) = returned {
            if error.kind() != std::io::ErrorKind::PermissionDenied {
                return Err(format!("wrong primary error: {error}").into());
            }
        } else {
            return Err(
                "execute did not return persistent cleanup failure within its bound".into(),
            );
        }
        let snapshot = snapshot?;
        if snapshot != b"preserved" || std::fs::read(&observed_paths.stdout)? != snapshot {
            return Err("partial evidence changed after return".into());
        }
        Ok(())
    }

    #[test]
    fn capture_stop_flushes_and_closes_reader_while_writer_remains_live()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        let (reader, mut writer) = std::os::unix::net::UnixStream::pair()?;
        let path = root.join("capture");
        let output = fresh_file(&path)?;
        let stop = Arc::new(AtomicBool::new(false));
        let (sender, receiver) = mpsc::channel();
        let worker = start_drain(
            reader,
            Some(output),
            Stream::Stdout,
            1024,
            sender,
            Arc::clone(&stop),
            None,
        )?;
        writer.write_all(b"preserved")?;
        let start = Instant::now();
        while std::fs::metadata(&path)?.len() != 9 && start.elapsed() < Duration::from_secs(1) {
            thread::sleep(POLL);
        }
        stop.store(true, Ordering::Relaxed);
        let returned = receiver.recv_timeout(Duration::from_secs(1));
        // A detached writer would accept and capture these bytes after return.
        let late_write = writer.write_all(b"late");
        drop(writer); // also releases the old blocking reader on RED
        worker.join().map_err(|_| "capture worker panicked")?;
        if let Ok((Stream::Stdout, Ok(result))) = returned {
            if result.bytes != 9 || late_write.is_ok() || std::fs::read(&path)? != b"preserved" {
                return Err("capture reader remained live or evidence changed".into());
            }
        } else {
            return Err("capture stop did not return while writer remained live".into());
        }
        Ok(())
    }

    fn wait_for_fixture_exit(pid: Pid) -> Result<(), Box<dyn std::error::Error>> {
        let start = Instant::now();
        loop {
            match rustix::process::test_kill_process(pid) {
                Err(rustix::io::Errno::SRCH) => return Ok(()),
                Ok(()) | Err(rustix::io::Errno::PERM)
                    if start.elapsed() < Duration::from_secs(2) =>
                {
                    thread::sleep(POLL);
                }
                result => return Err(format!("fixture {pid:?} was not reaped: {result:?}").into()),
            }
        }
    }
    #[test]
    fn waiter_creation_error_retains_primary_failure_source()
    -> Result<(), Box<dyn std::error::Error>> {
        let error = with_reaper_error(
            std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "primary signaling failure",
            )
            .into(),
            std::io::Error::other("thread unavailable"),
        );
        let BenchError::Io(error) = error else {
            return Err("lost I/O error classification".into());
        };
        if error.kind() != std::io::ErrorKind::PermissionDenied
            || !error.to_string().contains("thread unavailable")
        {
            return Err("lost failure details".into());
        }
        let detail = error.get_ref().ok_or("missing contextual error")?;
        if !detail
            .source()
            .is_some_and(|source| source.to_string().contains("primary signaling failure"))
        {
            return Err("lost primary error source".into());
        }
        Ok(())
    }
}
