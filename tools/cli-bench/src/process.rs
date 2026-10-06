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
/// Owns only policy; each execute call controls its separate process group.
/// Unconfirmed termination returns failure; a private waiter may reap later.
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
    /// cancellation and quota stops are retained in the returned outcome. Cleanup
    /// allows one second after TERM and one second after KILL for confirmation.
    /// Persistent failures stop/join captures and return error; a live direct child
    /// is handed to a private waiter until exit, with no capture files attached.
    pub fn execute(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
    ) -> Result<ProcessOutcome, BenchError> {
        self.execute_controlled(spec, paths, &NativeControl)
    }
    fn execute_controlled(
        &self,
        spec: &CommandSpec,
        paths: &CapturePaths,
        control: &impl GroupControl,
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
            )?);
            monitor(&mut owned, &receiver, workers.len(), start, &self.policy)
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
        Ok(outcome)
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
) -> std::io::Result<JoinHandle<()>> {
    thread::Builder::new()
        .name(format!("capture-{stream:?}"))
        .spawn(move || {
            let result = drain(reader, output, limit, &stop);
            let _ = sender.send((stream, result));
        })
}
fn drain(
    mut reader: impl Read + AsFd,
    mut output: Option<File>,
    limit: u64,
    stop: &AtomicBool,
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
    owned: &mut OwnedGroup<'_, impl GroupControl>,
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
