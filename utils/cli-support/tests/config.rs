#![allow(
    clippy::panic_in_result_fn,
    reason = "Test assertions fail the test; Result propagates fixture I/O errors."
)]

use cli_support::{Config, Destination, Format, Overrides};
use std::{ffi::OsString, path::PathBuf};
use tracing::level_filters::LevelFilter;

type Result = std::result::Result<(), Box<dyn std::error::Error>>;

#[test]
fn defaults_are_quiet() -> Result {
    let config = Config::resolve(Overrides::default(), |_| None)?;
    assert_eq!(config.level, LevelFilter::OFF);
    assert_eq!(config.format, Format::Text);
    assert_eq!(config.destination, Destination::Stderr);
    assert!(!config.timings);
    Ok(())
}

#[test]
fn resolves_each_field_and_ignores_overridden_invalid_environment() -> Result {
    let config = Config::resolve(
        Overrides {
            level: Some(LevelFilter::DEBUG),
            timings: Some(false),
            ..Overrides::default()
        },
        |key| {
            Some(OsString::from(match key {
                "CLIS_LOG_FORMAT" => "json",
                "CLIS_LOG_FILE" => "events.jsonl",
                _ => "invalid",
            }))
        },
    )?;
    assert_eq!(config.level, LevelFilter::DEBUG);
    assert_eq!(config.format, Format::Json);
    assert_eq!(
        config.destination,
        Destination::File(PathBuf::from("events.jsonl"))
    );
    assert!(!config.timings);
    Ok(())
}

#[test]
fn environment_enables_timings_independently() -> Result {
    let config = Config::resolve(Overrides::default(), |key| {
        (key == "CLIS_TIMINGS").then(|| OsString::from("true"))
    })?;
    assert!(config.timings);
    assert_eq!(config.level, LevelFilter::OFF);
    Ok(())
}

#[test]
fn rejects_invalid_effective_values() {
    for key in [
        "CLIS_LOG_LEVEL",
        "CLIS_LOG_FORMAT",
        "CLIS_TIMINGS",
        "CLIS_LOG_FILE",
    ] {
        let result = Config::resolve(Overrides::default(), |name| {
            (key == name).then(OsString::new)
        });
        assert!(
            result
                .err()
                .is_some_and(|error| error.to_string().contains(key))
        );
    }
}

#[test]
fn dash_explicitly_restores_stderr() -> Result {
    let config = Config::resolve(
        Overrides {
            destination: Some(Destination::Stderr),
            ..Overrides::default()
        },
        |key| (key == "CLIS_LOG_FILE").then(|| OsString::from("existing.log")),
    )?;
    assert_eq!(config.destination, Destination::Stderr);
    let config = Config::resolve(Overrides::default(), |key| {
        (key == "CLIS_LOG_FILE").then(|| OsString::from("-"))
    })?;
    assert_eq!(config.destination, Destination::Stderr);
    Ok(())
}

#[cfg(unix)]
#[test]
fn preserves_native_paths_but_rejects_non_utf8_settings() -> Result {
    use std::os::unix::ffi::OsStringExt;
    let path = OsString::from_vec(vec![b'x', 0xff]);
    let config = Config::resolve(Overrides::default(), |key| {
        (key == "CLIS_LOG_FILE").then(|| path.clone())
    })?;
    assert_eq!(config.destination, Destination::File(PathBuf::from(&path)));
    for key in ["CLIS_LOG_LEVEL", "CLIS_LOG_FORMAT", "CLIS_TIMINGS"] {
        assert!(
            Config::resolve(Overrides::default(), |name| {
                (key == name).then(|| path.clone())
            })
            .is_err()
        );
    }
    Ok(())
}
