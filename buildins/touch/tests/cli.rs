use assert_fs::{TempDir, prelude::*};
use cli_testing::{Command, cargo_bin_cmd};
use predicates::prelude::*;
use std::{
    fs,
    path::Path,
    time::{Duration, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn command(dir: &TempDir) -> Command {
    let mut cmd = cargo_bin_cmd!();
    cmd.current_dir(dir.path()).env("TZ", "UTC");
    cmd
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions fail the test; Result propagates fixture I/O errors."
)]
fn assert_modified(path: &Path, seconds: u64) -> Result<()> {
    assert_eq!(
        fs::metadata(path)?.modified()?.duration_since(UNIX_EPOCH)?,
        Duration::from_secs(seconds)
    );
    Ok(())
}

#[test]
fn usage() {
    cargo_bin_cmd!()
        .arg("--help")
        .assert()
        .code(0)
        .stdout(
            predicate::str::contains("Usage")
                .and(predicate::str::contains("2000-01-01 12:00:00"))
                .and(predicate::str::contains("1 hour"))
                .and(predicate::str::contains("@946684800")),
        )
        .stderr("");
}

#[test]
fn creates_single_file() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .arg("new")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("new")
        .assert(predicate::path::is_file())
        .assert("");
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions fail the test; Result propagates fixture I/O errors."
)]
fn preserves_contents_and_updates_existing_file() -> Result<()> {
    let dir = TempDir::new()?;
    let file = dir.child("existing");
    file.write_str("keep these contents\n")?;
    fs::File::open(file.path())?.set_modified(UNIX_EPOCH)?;
    command(&dir)
        .arg("existing")
        .assert()
        .success()
        .stdout("")
        .stderr("");
    file.assert("keep these contents\n");
    assert!(fs::metadata(file.path())?.modified()? > UNIX_EPOCH);
    Ok(())
}

#[test]
fn sets_supported_numeric_timestamps_on_multiple_files() -> Result<()> {
    let dir = TempDir::new()?;
    for (timestamp, seconds) in [
        ("200001010000", 946_684_800),
        ("200001010000.07", 946_684_807),
        ("200001010000.60", 946_684_860),
    ] {
        dir.child("existing").write_str("preserve me")?;
        let new = dir.child(format!("new-{timestamp}"));
        command(&dir)
            .args(["-t", timestamp, "existing"])
            .arg(new.path())
            .assert()
            .success()
            .stdout("")
            .stderr("");
        dir.child("existing").assert("preserve me");
        new.assert("");
        assert_modified(dir.child("existing").path(), seconds)?;
        assert_modified(new.path(), seconds)?;
    }
    Ok(())
}

#[test]
fn c_does_not_create_missing_files() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["-c", "missing", "also-missing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    for name in ["missing", "also-missing"] {
        dir.child(name).assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn c_still_updates_existing_files() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("existing").write_str("keep me")?;
    command(&dir)
        .args(["-c", "-t", "200001010000.07", "missing", "existing"])
        .assert()
        .success()
        .stdout("")
        .stderr("");
    dir.child("missing").assert(predicate::path::missing());
    dir.child("existing").assert("keep me");
    assert_modified(dir.child("existing").path(), 946_684_807)
}

#[test]
fn rejects_invalid_timestamps_without_creating_files() -> Result<()> {
    let dir = TempDir::new()?;
    for timestamp in ["bad", "202413010000", "202402300000"] {
        command(&dir)
            .args(["-t", timestamp, "new"])
            .assert()
            .code(2)
            .stdout("")
            .stderr(predicate::str::contains("Invalid timestamp format"));
        dir.child("new").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn reports_creation_path_errors() -> Result<()> {
    let dir = TempDir::new()?;
    dir.child("file").write_str("keep me")?;
    for path in ["missing/child", "file/child"] {
        command(&dir)
            .arg(path)
            .assert()
            .code(1)
            .stdout("")
            .stderr(predicate::str::contains(path));
    }
    dir.child("file").assert("keep me");
    dir.child("missing").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn requires_file_operand() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .assert()
        .failure()
        .stdout("")
        .stderr(predicate::str::is_empty().not());
    Ok(())
}

#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions report CLI regressions."
)]
fn assert_times(path: &Path, access: u64, modified: u64) -> Result<()> {
    let metadata = fs::metadata(path)?;
    assert_eq!(
        metadata.accessed()?,
        UNIX_EPOCH
            .checked_add(Duration::from_secs(access))
            .ok_or("time overflow")?
    );
    assert_eq!(
        metadata.modified()?,
        UNIX_EPOCH
            .checked_add(Duration::from_secs(modified))
            .ok_or("time overflow")?
    );
    Ok(())
}

fn existing(dir: &TempDir) -> Result<()> {
    dir.child("existing").write_str("keep me")?;
    fs::File::open(dir.child("existing").path())?.set_times(
        fs::FileTimes::new()
            .set_accessed(
                UNIX_EPOCH
                    .checked_add(Duration::from_secs(100))
                    .ok_or("time overflow")?,
            )
            .set_modified(
                UNIX_EPOCH
                    .checked_add(Duration::from_secs(200))
                    .ok_or("time overflow")?,
            ),
    )?;
    Ok(())
}

#[test]
fn selects_access_and_modification_times() -> Result<()> {
    for (flags, access, modified) in [
        (vec![], 946_684_807, 946_684_807),
        (vec!["-a"], 946_684_807, 200),
        (vec!["--access"], 946_684_807, 200),
        (vec!["-m"], 100, 946_684_807),
        (vec!["--modify"], 100, 946_684_807),
        (vec!["-am"], 946_684_807, 946_684_807),
        (vec!["--time=atime"], 946_684_807, 200),
        (vec!["--time=use"], 946_684_807, 200),
        (vec!["--time=mtime"], 100, 946_684_807),
    ] {
        let dir = TempDir::new()?;
        existing(&dir)?;
        command(&dir)
            .args(flags)
            .args(["-t", "200001010000.07", "existing"])
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("existing").path(), access, modified)?;
        dir.child("existing").assert("keep me");
    }
    Ok(())
}

#[test]
fn reference_copies_distinct_times() -> Result<()> {
    let dir = TempDir::new()?;
    existing(&dir)?;
    command(&dir)
        .args(["--reference=existing", "new"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("new").path(), 100, 200)
}

#[test]
fn parses_bsd_and_gnu_dates() -> Result<()> {
    for (date, seconds) in [
        ("2000-01-01T00:00:07Z", 946_684_807),
        ("2000-01-01 00:00:07", 946_684_807),
        ("@946684807", 946_684_807),
        ("2000-01-01 UTC +7 seconds", 946_684_807),
        ("2000-01-01T01:00:07+01:00", 946_684_807),
    ] {
        let dir = TempDir::new()?;
        command(&dir)
            .args(["--date", date, "new"])
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("new").path(), seconds, seconds)?;
    }
    Ok(())
}

#[test]
fn numeric_time_uses_local_timezone_and_two_digit_year() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .env("TZ", "America/New_York")
        .args(["-t", "0001010000.07", "new"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("new").path(), 946_702_807, 946_702_807)
}

#[test]
fn bsd_adjustment_preserves_difference_and_skips_missing() -> Result<()> {
    let dir = TempDir::new()?;
    existing(&dir)?;
    command(&dir)
        .args(["-A", "-01", "existing", "missing"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("existing").path(), 99, 199)?;
    dir.child("missing").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn continues_after_a_file_error() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["missing/child", "new"])
        .assert()
        .code(1)
        .stdout("")
        .stderr(predicate::str::contains("missing/child"));
    dir.child("new").assert("");
    Ok(())
}

#[test]
fn dash_and_dash_prefixed_names_are_files() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["--", "-", "-file"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    dir.child("-").assert("");
    dir.child("-file").assert("");
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions report CLI timestamps."
)]
fn bsd_legacy_timestamp_operand() -> Result<()> {
    let dir = TempDir::new()?;
    let before = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)?
        .as_secs();
    command(&dir)
        .args(["0101000000", "new"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    dir.child("0101000000").assert(predicate::path::missing());
    let after = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)?
        .as_secs();
    let time = fs::metadata(dir.child("new").path())?
        .modified()?
        .duration_since(UNIX_EPOCH)?
        .as_secs();
    assert!((before..=after).any(|now| 946_684_800_u64.checked_add(now % 60) == Some(time)));
    Ok(())
}

#[cfg(unix)]
#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions report CLI regressions."
)]
fn updates_symlink_without_touching_target_or_creating_files() -> Result<()> {
    use std::os::unix::fs::symlink;
    let dir = TempDir::new()?;
    existing(&dir)?;
    symlink("existing", dir.child("link").path())?;
    symlink("absent", dir.child("dangling").path())?;
    command(&dir)
        .args(["-h", "-t", "200001010000.07", "link", "dangling", "missing"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("existing").path(), 100, 200)?;
    for name in ["link", "dangling"] {
        assert_eq!(
            fs::symlink_metadata(dir.child(name).path())?.modified()?,
            UNIX_EPOCH + Duration::from_secs(946_684_807)
        );
    }
    dir.child("missing").assert(predicate::path::missing());
    dir.child("absent").assert(predicate::path::missing());
    Ok(())
}

#[test]
fn reference_plus_date_offsets_both_times_in_either_order() -> Result<()> {
    let dir = TempDir::new()?;
    existing(&dir)?;
    for (flags, modified) in [
        (["-r", "existing", "-d", "1 second"], 200),
        (["-d", "1 second", "-r", "existing"], 200),
        (["-r", "existing", "-d", "1 second"], 100),
        (["-d", "1 second", "-r", "existing"], 100),
    ] {
        fs::File::open(dir.child("existing").path())?
            .set_modified(UNIX_EPOCH + Duration::from_secs(modified))?;
        command(&dir)
            .args(flags)
            .arg("new")
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("new").path(), 101, modified + 1)?;
    }
    Ok(())
}

#[test]
fn last_explicit_timestamp_overrides_previous_source() -> Result<()> {
    let dir = TempDir::new()?;
    existing(&dir)?;
    for (flags, expected) in [
        (vec!["-d", "@100", "-t", "200001010000"], 946_684_800),
        (vec!["-t", "200001010000", "-d", "@100"], 100),
        (vec!["-r", "existing", "-t", "200001010000"], 946_684_800),
        (
            vec!["-t", "200101010000", "-t", "200001010000"],
            946_684_800,
        ),
    ] {
        command(&dir)
            .args(flags)
            .arg("new")
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("new").path(), expected, expected)?;
    }
    Ok(())
}

#[test]
#[allow(
    clippy::panic_in_result_fn,
    reason = "Assertions report filesystem timestamps."
)]
fn fractional_and_pre_epoch_dates() -> Result<()> {
    let dir = TempDir::new()?;
    for date in [
        "2000-01-01T00:00:07,123456789Z",
        "2000-01-01T00:00:07.1234567891",
    ] {
        command(&dir)
            .args(["-d", date, "new"])
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_eq!(
            fs::metadata(dir.child("new").path())?.modified()?,
            UNIX_EPOCH + Duration::new(946_684_807, 123_456_789)
        );
    }
    for date in ["@-0.5", "1969-12-31T23:59:59.5"] {
        command(&dir)
            .args(["-d", date, "new"])
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_eq!(
            fs::metadata(dir.child("new").path())?.modified()?,
            UNIX_EPOCH
                .checked_sub(Duration::from_millis(500))
                .ok_or("time underflow")?
        );
    }
    Ok(())
}

#[test]
fn rejects_invalid_dates_adjustments_and_missing_references_before_changes() -> Result<()> {
    let dir = TempDir::new()?;
    for (flags, status) in [
        (vec!["-d", "nonsense"], 1),
        (vec!["-f"], 2),
        (vec!["--force"], 2),
        (vec!["-d", "2024-02-30T00:00:00Z"], 1),
        (vec!["-d", "2024-03-10T02:30:00"], 1),
        (vec!["-d", "2024-03-10T02:30"], 1),
        (vec!["-d", "2024-03-10 02:30"], 1),
        (vec!["-d", "2024-03-10 02:30:00 "], 1),
        (vec!["-d", "2024-03-10T02:30:00.1234567891"], 1),
        (vec!["-d", "2024-01-01T00:00:60"], 1),
        (vec!["-t", "202403100230"], 2),
        (vec!["-A", "xx"], 2),
        (vec!["-A", "123"], 2),
        (vec!["-A", "12345678"], 2),
        (vec!["--time=ctime"], 2),
        (vec!["-r", "missing"], 1),
    ] {
        command(&dir)
            .env("TZ", "America/New_York")
            .args(flags)
            .arg("new")
            .assert()
            .code(status)
            .stdout("")
            .stderr(predicate::str::is_empty().not());
        dir.child("new").assert(predicate::path::missing());
    }
    Ok(())
}

#[test]
fn long_options() -> Result<()> {
    let dir = TempDir::new()?;
    command(&dir)
        .args(["--no-create", "missing"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    dir.child("missing").assert(predicate::path::missing());
    command(&dir)
        .args(["--timestamp", "200001010000", "new"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("new").path(), 946_684_800, 946_684_800)?;
    command(&dir)
        .args(["--adjustment=-0130", "new"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("new").path(), 946_684_710, 946_684_710)
}

#[cfg(unix)]
#[test]
fn follows_symlinks_and_creates_dangling_targets() -> Result<()> {
    use std::os::unix::fs::symlink;
    let dir = TempDir::new()?;
    symlink("target", dir.child("link").path())?;
    command(&dir)
        .args(["-t", "200001010000", "link"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("target").path(), 946_684_800, 946_684_800)
}

#[cfg(unix)]
#[test]
fn updates_directories_fifos_and_unreadable_files() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let dir = TempDir::new()?;
    dir.child("directory").create_dir_all()?;
    existing(&dir)?;
    fs::set_permissions(
        dir.child("existing").path(),
        fs::Permissions::from_mode(0o200),
    )?;
    if !std::process::Command::new("mkfifo")
        .arg(dir.child("pipe").path())
        .status()?
        .success()
    {
        return Err("mkfifo failed".into());
    }
    command(&dir)
        .timeout(Duration::from_secs(5))
        .args(["-t", "200001010000", "directory", "pipe", "existing"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    for name in ["directory", "pipe", "existing"] {
        assert_times(dir.child(name).path(), 946_684_800, 946_684_800)?;
    }
    Ok(())
}

#[test]
fn bsd_adjustment_normalizes_duration_components() -> Result<()> {
    for (adjustment, expected) in [("60", 260), ("9900", 6_140), ("0060", 260)] {
        let dir = TempDir::new()?;
        existing(&dir)?;
        command(&dir)
            .args(["-m", "-A", adjustment, "existing"])
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("existing").path(), 100, expected)?;
    }
    Ok(())
}

#[test]
fn bsd_adjustment_consumes_but_does_not_use_legacy_time() -> Result<()> {
    let dir = TempDir::new()?;
    existing(&dir)?;
    command(&dir)
        .args(["-A", "01", "0101000000", "existing"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("existing").path(), 101, 201)?;
    dir.child("0101000000").assert(predicate::path::missing());
    Ok(())
}

#[cfg(unix)]
#[test]
fn no_dereference_copies_reference_symlink_times() -> Result<()> {
    use std::os::unix::fs::symlink;
    let dir = TempDir::new()?;
    existing(&dir)?;
    dir.child("destination").touch()?;
    symlink("existing", dir.child("reference-link").path())?;
    let times = rustix::fs::Timestamps {
        last_access: rustix::fs::Timespec {
            tv_sec: 300,
            tv_nsec: 0,
        },
        last_modification: rustix::fs::Timespec {
            tv_sec: 400,
            tv_nsec: 0,
        },
    };
    rustix::fs::utimensat(
        rustix::fs::CWD,
        dir.child("reference-link").path(),
        &times,
        rustix::fs::AtFlags::SYMLINK_NOFOLLOW,
    )?;
    command(&dir)
        .args(["-h", "-r", "reference-link", "destination"])
        .assert()
        .code(0)
        .stdout("")
        .stderr("");
    assert_times(dir.child("destination").path(), 300, 400)?;
    assert_times(dir.child("existing").path(), 100, 200)
}

#[test]
fn ambiguous_local_timestamp_uses_first_occurrence() -> Result<()> {
    let dir = TempDir::new()?;
    for flags in [
        ["-t", "202411030130"],
        ["-d", "2024-11-03 01:30:00"],
        ["-d", "2024-11-03 01:30"],
        ["-d", "2024-11-03T01:30"],
    ] {
        command(&dir)
            .env("TZ", "America/New_York")
            .args(flags)
            .arg("new")
            .assert()
            .code(0)
            .stdout("")
            .stderr("");
        assert_times(dir.child("new").path(), 1_730_611_800, 1_730_611_800)?;
    }
    Ok(())
}
