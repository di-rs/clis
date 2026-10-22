//! Bounded PTY driver. Output is a combined terminal stream, rendered by vt100.
//! Only descendants of this test's shell are considered for forced cleanup.

use std::collections::{HashMap, HashSet};
use std::io::{Read as _, Write as _};
use std::path::Path;
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::time::{Duration, Instant};

use anyhow::{Context as _, bail};
use portable_pty::{Child, CommandBuilder, MasterPty, PtySize, native_pty_system};

type WriteRequest = (Vec<u8>, SyncSender<std::io::Result<()>>);

pub struct Pty {
    child: Box<dyn Child + Send + Sync>,
    _master: Box<dyn MasterPty + Send>,
    writer: SyncSender<WriteRequest>,
    receiver: Receiver<Vec<u8>>,
    terminal: vt100::Parser,
    output: Vec<u8>,
    known_children: HashMap<sysinfo::Pid, u64>,
}

impl Pty {
    pub fn new(home: &Path, args: &[&str], env: &[(&str, &str)]) -> anyhow::Result<Self> {
        Self::with_prompt(home, args, env, Some("shish-test> "))
    }

    pub fn with_prompt(
        home: &Path,
        args: &[&str],
        env: &[(&str, &str)],
        prompt: Option<&str>,
    ) -> anyhow::Result<Self> {
        let pair = native_pty_system().openpty(PtySize {
            rows: 30,
            cols: 120,
            pixel_width: 0,
            pixel_height: 0,
        })?;
        let cli = cli_testing::cargo_bin_cmd!();
        let mut command = CommandBuilder::new(cli.get_program());
        command.args(args);
        command.env_clear();
        for (key, value) in [
            ("PATH", "/usr/bin:/bin"),
            ("LC_ALL", "C"),
            ("TZ", "UTC"),
            ("TERM", "xterm-256color"),
            ("NO_COLOR", "1"),
            ("PS2", "more> "),
            ("SANCTUARY_LOG_LEVEL", "off"),
        ] {
            command.env(key, value);
        }
        command.env("HOME", home);
        if let Some(prompt) = prompt {
            command.env("PS1", prompt);
        }
        command.env("HISTFILE", home.join("history"));
        command.cwd(home);
        for (key, value) in env {
            command.env(key, value);
        }
        let child = pair.slave.spawn_command(command)?;
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader()?;
        let mut writer = pair.master.take_writer()?;
        let (requests, pending) = mpsc::sync_channel::<WriteRequest>(1);
        // Keep blocking OS writes off the driver thread so backpressure cannot
        // prevent its deadline or cleanup guard from running. Killing the PTY
        // child on cleanup releases a writer blocked on terminal input.
        std::thread::spawn(move || {
            while let Ok((bytes, completed)) = pending.recv() {
                let result = writer.write_all(&bytes).and_then(|()| writer.flush());
                let failed = result.is_err();
                if completed.send(result).is_err() || failed {
                    break;
                }
            }
        });
        let (sender, receiver) = mpsc::sync_channel(64);
        std::thread::spawn(move || {
            let mut buffer = [0; 4096];
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) | Err(_) => break,
                    Ok(size) => {
                        if sender
                            .send(buffer.get(..size).unwrap_or_default().to_vec())
                            .is_err()
                        {
                            break;
                        }
                    }
                }
            }
        });
        Ok(Self {
            child,
            _master: pair.master,
            writer: requests,
            receiver,
            terminal: vt100::Parser::new(30, 120, 1000),
            output: Vec::new(),
            known_children: HashMap::new(),
        })
    }

    pub fn send(&self, input: &str) -> anyhow::Result<()> {
        let (completed, result) = mpsc::sync_channel(1);
        self.writer
            .try_send((input.as_bytes().to_vec(), completed))
            .context("PTY write queue unavailable")?;
        result
            .recv_timeout(Duration::from_secs(1))
            .context("PTY write did not complete within one second")??;
        Ok(())
    }

    pub fn expect(&mut self, text: &str) -> anyhow::Result<()> {
        self.wait_for(text, |contents| contents.contains(text))
    }

    /// Wait for the current input line, excluding only terminal padding.
    pub fn expect_line(&mut self, text: &str) -> anyhow::Result<()> {
        self.wait_for(text, |contents| {
            contents
                .lines()
                .last()
                .is_some_and(|line| line.trim_end() == text.trim_end())
        })
    }

    fn wait_for(&mut self, text: &str, matches: impl Fn(&str) -> bool) -> anyhow::Result<()> {
        let started = Instant::now();
        while !matches(&self.terminal.screen().contents()) {
            if started.elapsed() >= Duration::from_secs(6) {
                bail!(
                    "timed out waiting for {text:?}; screen: {:?}",
                    self.terminal.screen().contents()
                );
            }
            self.pump()?;
        }
        Ok(())
    }

    pub fn finish(&mut self, status: u32) -> anyhow::Result<()> {
        let started = Instant::now();
        loop {
            if let Some(result) = self.child.try_wait()? {
                anyhow::ensure!(
                    result.exit_code() == status,
                    "unexpected PTY status {result:?}; screen: {:?}",
                    self.terminal.screen().contents()
                );
                return Ok(());
            }
            anyhow::ensure!(
                started.elapsed() < Duration::from_secs(6),
                "shell did not exit; screen: {:?}",
                self.terminal.screen().contents()
            );
            self.pump()?;
        }
    }

    fn pump(&mut self) -> anyhow::Result<()> {
        self.observe_children();
        match self.receiver.recv_timeout(Duration::from_millis(25)) {
            Ok(bytes) => {
                let start = self.output.len().saturating_sub(3);
                self.output.extend_from_slice(&bytes);
                anyhow::ensure!(self.output.len() <= 1_048_576, "PTY exceeded output budget");
                self.terminal.process(&bytes);
                // Reedline asks the terminal for its cursor position. A PTY is
                // only transport; the driver must supply this terminal reply.
                let requests = self
                    .output
                    .get(start..)
                    .context("invalid transcript offset")?
                    .windows(4)
                    .filter(|window| *window == b"\x1b[6n")
                    .count();
                if requests > 0 {
                    let (row, col) = self.terminal.screen().cursor_position();
                    let reply =
                        format!("\x1b[{};{}R", row.saturating_add(1), col.saturating_add(1));
                    // One deadline covers the whole batch, even for a program
                    // flooding cursor queries without consuming their replies.
                    self.send(&reply.repeat(requests))?;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout | mpsc::RecvTimeoutError::Disconnected) => {}
        }
        Ok(())
    }

    fn observe_children(&mut self) {
        let Some(root) = self.child.process_id().map(sysinfo::Pid::from_u32) else {
            return;
        };
        let mut system = sysinfo::System::new();
        system.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        let mut parents = HashSet::from([root]);
        loop {
            let descendants: Vec<_> = system
                .processes()
                .iter()
                .filter_map(|(pid, process)| {
                    (process
                        .parent()
                        .is_some_and(|parent| parents.contains(&parent))
                        && !parents.contains(pid))
                    .then_some(*pid)
                })
                .collect();
            if descendants.is_empty() {
                break;
            }
            parents.extend(descendants);
        }
        parents.remove(&root);
        for pid in parents {
            if let Some(process) = system.process(pid) {
                self.known_children.insert(pid, process.start_time());
            }
        }
    }
}

impl Drop for Pty {
    fn drop(&mut self) {
        self.observe_children();
        let mut system = sysinfo::System::new();
        let children: Vec<_> = self.known_children.keys().copied().collect();
        system.refresh_processes(sysinfo::ProcessesToUpdate::Some(&children), true);
        for child in children {
            if let Some(process) = system.process(child)
                && self.known_children.get(&child) == Some(&process.start_time())
            {
                process.kill();
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
