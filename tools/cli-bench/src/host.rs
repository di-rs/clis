use crate::{HostMetadata, HostValue};
use std::{
    collections::BTreeMap,
    io::Read,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

/// Collect bounded native probes and redact values outside the documented child settings.
/// Does not enumerate the process environment, set umask, or initialize global state.
#[must_use]
pub fn collect_host(settings: &BTreeMap<String, String>) -> HostMetadata {
    let settings = settings
        .iter()
        .filter_map(|(key, value)| {
            let allowed = match key.as_str() {
                "LC_ALL" => value == "C",
                "TZ" => value == "UTC",
                "CLIS_LOG_LEVEL" => matches!(
                    value.as_str(),
                    "off" | "error" | "warn" | "info" | "debug" | "trace"
                ),
                _ => return None,
            };
            Some((
                key.clone(),
                if allowed {
                    value.clone()
                } else {
                    "[redacted]".into()
                },
            ))
        })
        .collect();
    let (cpu, memory) = if cfg!(target_os = "macos") {
        (
            probe("/usr/sbin/sysctl", &["-n", "machdep.cpu.brand_string"]),
            probe("/usr/sbin/sysctl", &["-n", "hw.memsize"]),
        )
    } else if cfg!(target_os = "linux") {
        (
            read_field("/proc/cpuinfo", "model name"),
            read_field("/proc/meminfo", "MemTotal"),
        )
    } else {
        (
            unavailable("unsupported platform"),
            unavailable("unsupported platform"),
        )
    };
    HostMetadata {
        os: std::env::consts::OS.into(),
        architecture: std::env::consts::ARCH.into(),
        kernel: probe("/usr/bin/uname", &["-sr"]),
        cpu,
        memory,
        filesystem: unavailable("no filesystem probe requested"),
        inherited_umask: if cfg!(target_os = "linux") {
            read_field("/proc/self/status", "Umask")
        } else {
            unavailable("no non-mutating native umask probe")
        },
        settings,
    }
}
fn unavailable(reason: &str) -> HostValue {
    HostValue::Unavailable(reason.into())
}
fn read_field(path: &str, key: &str) -> HostValue {
    let result = (|| -> std::io::Result<String> {
        let mut text = String::new();
        std::fs::File::open(path)?
            .take(65_536)
            .read_to_string(&mut text)?;
        Ok(text)
    })();
    match result {
        Ok(text) => text
            .lines()
            .find_map(|line| {
                line.split_once(':')
                    .filter(|(name, _)| name.trim() == key)
                    .map(|(_, value)| HostValue::Available(value.trim().to_owned()))
            })
            .unwrap_or_else(|| unavailable("field unavailable")),
        Err(error) => HostValue::Unavailable(error.to_string()),
    }
}
fn probe(program: &str, args: &[&str]) -> HostValue {
    let Ok(mut child) = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return unavailable("probe unavailable");
    };
    let Some(stdout) = child.stdout.take() else {
        let _ = child.kill();
        let _ = child.wait();
        return unavailable("probe stream unavailable");
    };
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.take(4097).read_to_end(&mut bytes).map(|_| bytes)
    });
    let start = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if start.elapsed() < Duration::from_secs(2) => {
                std::thread::sleep(Duration::from_millis(10));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };
    match (status, reader.join()) {
        (Some(status), Ok(Ok(bytes)))
            if status.success() && !bytes.is_empty() && bytes.len() <= 4096 =>
        {
            String::from_utf8(bytes).map_or_else(
                |_| unavailable("non-UTF-8 probe output"),
                |text| HostValue::Available(text.trim().into()),
            )
        }
        _ => unavailable("probe failed, timed out or exceeded output limit"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn host_metadata_does_not_dump_environment() {
        let settings = [
            ("SECRET".into(), "credential".into()),
            ("LC_ALL".into(), "C".into()),
            ("TZ".into(), "secret-value".into()),
        ]
        .into();
        let host = collect_host(&settings);
        assert_eq!(host.settings.get("LC_ALL").map(String::as_str), Some("C"));
        assert!(!host.settings.contains_key("SECRET"));
        assert_ne!(
            host.settings.get("TZ").map(String::as_str),
            Some("secret-value")
        );
        assert_ne!(host.os, "");
    }
}
