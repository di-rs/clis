use cli_bench::{BenchError, MeasurementLock, Store};
use std::{
    fs,
    path::Path,
    process::{Child, Command, Stdio},
    sync::atomic::AtomicBool,
    time::{Duration, Instant},
};
type TestResult = Result<(), Box<dyn std::error::Error>>;
struct ChildOwner(Child);
impl Drop for ChildOwner {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn wait_for(path: &Path, child: &mut Child) -> TestResult {
    let start = Instant::now();
    while !path.exists() {
        if let Some(status) = child.try_wait()? {
            return Err(format!("lock child exited {status}").into());
        }
        if start.elapsed() > Duration::from_secs(90) {
            return Err("bounded lock child wait expired".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}
fn spawn(root: &Path, hold: bool) -> Result<ChildOwner, std::io::Error> {
    Command::new(std::env::current_exe()?)
        .args(["--exact", "lock_child", "--nocapture"])
        .current_dir(root)
        .env(
            "CLI_BENCH_LOCK_CHILD",
            if hold { "hold" } else { "release" },
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .map(ChildOwner)
}
#[test]
fn lock_child() -> TestResult {
    let Some(mode) = std::env::var_os("CLI_BENCH_LOCK_CHILD") else {
        return Ok(());
    };
    let root = std::env::current_dir()?;
    let held = MeasurementLock::acquire(&AtomicBool::new(false), || {
        fs::write(root.join("waiting"), b"waiting")?;
        Ok(())
    })?;
    let store = Store::open(&root.join("different-data-directory"))?;
    fs::write(
        root.join("acquired"),
        format!(
            "{}\n{}",
            held.wait_duration().as_secs_f64(),
            store.root().display()
        ),
    )?;
    if mode == "hold" {
        std::thread::sleep(Duration::from_secs(90));
    }
    Ok(())
}
#[test]
fn different_processes_and_data_directories_contend_then_kill_releases_lock() -> TestResult {
    let first = assert_fs::TempDir::new()?;
    let second = assert_fs::TempDir::new()?;
    let mut owner = spawn(first.path(), true)?;
    wait_for(&first.join("acquired"), &mut owner.0)?;
    let mut contender = spawn(second.path(), false)?;
    wait_for(&second.join("waiting"), &mut contender.0)?;
    require(
        !second.join("acquired").exists(),
        "contender bypassed held lock",
    )?;
    std::thread::sleep(Duration::from_millis(60));
    owner.0.kill()?;
    require(
        !owner.0.wait()?.success(),
        &format!(
            "assertion failed: {}",
            stringify!(!owner.0.wait()?.success())
        ),
    )?;
    wait_for(&second.join("acquired"), &mut contender.0)?;
    let retained = fs::read_to_string(second.join("acquired"))?;
    let wait: f64 = retained
        .lines()
        .next()
        .ok_or("missing wait duration")?
        .parse()?;
    require(wait >= 0.05, "waiting was not recorded separately")?;
    // A new process can acquire the same persistent file after abnormal exit.
    let cancellation = AtomicBool::new(false);
    let _held = MeasurementLock::acquire(&cancellation, || Ok::<(), BenchError>(()))?;
    Ok(())
}

#[test]
fn cli_build_wait_is_reported_and_sigterm_cancels_before_preparation() -> TestResult {
    cancelled_cli_wait(rustix::process::Signal::TERM, 143)
}
#[test]
fn cli_build_wait_is_reported_and_sigint_cancels_before_preparation() -> TestResult {
    cancelled_cli_wait(rustix::process::Signal::INT, 130)
}
fn cancelled_cli_wait(signal: rustix::process::Signal, code: i32) -> TestResult {
    let first = assert_fs::TempDir::new()?;
    let second = assert_fs::TempDir::new()?;
    let mut owner = spawn(first.path(), true)?;
    wait_for(&first.join("acquired"), &mut owner.0)?;
    let stderr_path = second.join("diagnostic");
    let mut waiting = ChildOwner(
        Command::new(assert_cmd::cargo::cargo_bin!("cli-bench"))
            .args([
                "build",
                "--package",
                "missing",
                "--revision",
                "HEAD",
                "--data-dir",
            ])
            .arg(second.join("evidence"))
            .current_dir(second.path())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(fs::File::create(&stderr_path)?)
            .spawn()?,
    );
    let start = Instant::now();
    while !fs::read_to_string(&stderr_path)?.contains("Waiting for the per-user") {
        if let Some(status) = waiting.0.try_wait()? {
            return Err(format!("CLI exited before wait notice: {status}").into());
        }
        if start.elapsed() > Duration::from_secs(10) {
            return Err("CLI wait notice timeout".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    rustix::process::kill_process(
        rustix::process::Pid::from_raw(i32::try_from(waiting.0.id())?).ok_or("child pid")?,
        signal,
    )?;
    let start = Instant::now();
    let status = loop {
        if let Some(status) = waiting.0.try_wait()? {
            break status;
        }
        if start.elapsed() > Duration::from_secs(3) {
            return Err("CLI cancellation was not responsive".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    require(
        (status.code()) == (Some(code)),
        "CLI did not propagate cancellable wait failure",
    )?;
    require(
        fs::read_to_string(stderr_path)?.contains("cancelled while acquiring measurement lock"),
        &format!(
            "assertion failed: {}",
            stringify!(
                fs::read_to_string(stderr_path)?
                    .contains("cancelled while acquiring measurement lock")
            )
        ),
    )?;
    require(
        !second.join("evidence").exists(),
        "preparation began before acquisition",
    )?;
    Ok(())
}

fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
