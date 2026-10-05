#![allow(
    clippy::panic_in_result_fn,
    reason = "Test assertions fail the test; Result propagates fixture I/O errors."
)]

use std::io::{self, Write};

use biggie::{GenerationOptions, LineEnding, generate};

fn output(options: &GenerationOptions) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    generate(&mut bytes, options)?;
    Ok(bytes)
}

#[test]
fn seeds_reproduce_whole_stream_without_resetting_each_line() -> io::Result<()> {
    let options = GenerationOptions {
        lines: 30,
        seed: Some(0),
        ..GenerationOptions::default()
    };
    let first = output(&options)?;
    assert_eq!(first, output(&options)?);
    let other = output(&GenerationOptions {
        seed: Some(1),
        ..options.clone()
    })?;
    assert_ne!(first, other);
    let lines: std::collections::HashSet<_> = first.split(|&b| b == b'\n').collect();
    assert!(lines.len() > 2);
    let prefix = output(&GenerationOptions {
        lines: 2,
        ..options
    })?;
    assert!(first.starts_with(&prefix));
    Ok(())
}

#[test]
fn inclusive_ranges_vary_word_counts_and_lengths() -> io::Result<()> {
    let bytes = output(&GenerationOptions {
        lines: 100,
        words_per_line: 1..=3,
        word_length: 1..=4,
        seed: Some(42),
        ..GenerationOptions::default()
    })?;
    let mut counts = std::collections::BTreeSet::new();
    let mut lengths = std::collections::BTreeSet::new();
    for line in bytes.split(|&b| b == b'\n').filter(|line| !line.is_empty()) {
        let words: Vec<_> = line.split(|&b| b == b' ').collect();
        counts.insert(words.len());
        for word in words {
            lengths.insert(word.len());
            assert!(word.iter().all(u8::is_ascii_alphanumeric));
        }
    }
    assert_eq!(counts, std::collections::BTreeSet::from([1, 2, 3]));
    assert_eq!(lengths, std::collections::BTreeSet::from([1, 2, 3, 4]));
    Ok(())
}

#[test]
fn invalid_direct_options_fail_before_writing_even_for_zero_lines() {
    let defaults = GenerationOptions::default();
    for options in [
        GenerationOptions {
            words_per_line: std::ops::RangeInclusive::new(3, 2),
            ..defaults.clone()
        },
        GenerationOptions {
            word_length: std::ops::RangeInclusive::new(3, 2),
            ..defaults.clone()
        },
        GenerationOptions {
            word_length: 0..=2,
            ..defaults
        },
    ] {
        for lines in [0, 1] {
            let mut bytes = b"keep me".to_vec();
            let result = generate(
                &mut bytes,
                &GenerationOptions {
                    lines,
                    ..options.clone()
                },
            );
            assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::InvalidInput));
            assert_eq!(bytes, b"keep me");
        }
    }
}

#[test]
fn long_words_cross_buffer_boundaries_without_extra_bytes() -> io::Result<()> {
    let bytes = output(&GenerationOptions {
        lines: 1,
        words_per_line: 1..=1,
        word_length: 32_769..=32_769,
        seed: Some(7),
        final_newline: false,
        ..GenerationOptions::default()
    })?;
    assert_eq!(bytes.len(), 32_769);
    assert!(bytes.iter().all(u8::is_ascii_alphanumeric));
    Ok(())
}

#[test]
fn line_endings_do_not_change_seeded_content() -> io::Result<()> {
    let options = GenerationOptions {
        lines: 3,
        seed: Some(17),
        ..GenerationOptions::default()
    };
    let lf = output(&options)?;
    let crlf = output(&GenerationOptions {
        line_ending: LineEnding::CrLf,
        ..options.clone()
    })?;
    assert_eq!(crlf.split(|&b| b == b'\r').count(), 4);
    assert_eq!(
        crlf.into_iter().filter(|&b| b != b'\r').collect::<Vec<_>>(),
        lf
    );
    let unterminated = output(&GenerationOptions {
        final_newline: false,
        ..options
    })?;
    let mut terminated = unterminated;
    terminated.push(b'\n');
    assert_eq!(terminated, lf);
    Ok(())
}

#[test]
fn configured_generation_leaves_flushing_to_the_caller() -> io::Result<()> {
    let mut writer = io::BufWriter::new(FailingWriter);
    generate(
        &mut writer,
        &GenerationOptions {
            lines: 1,
            seed: Some(1),
            ..GenerationOptions::default()
        },
    )?;
    assert!(
        writer
            .flush()
            .is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe)
    );
    Ok(())
}

#[test]
fn configured_generation_propagates_write_errors() {
    let result = generate(
        FailingWriter,
        &GenerationOptions {
            lines: 1,
            ..GenerationOptions::default()
        },
    );
    assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe));
}

#[test]
fn zero_records_never_write_a_terminator() -> io::Result<()> {
    generate(
        FailingWriter,
        &GenerationOptions {
            lines: 0,
            ..GenerationOptions::default()
        },
    )?;
    Ok(())
}

struct FailingWriter;
impl Write for FailingWriter {
    fn write(&mut self, _bytes: &[u8]) -> io::Result<usize> {
        Err(io::ErrorKind::BrokenPipe.into())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn library_propagates_write_errors_without_cli_setup() {
    let result = biggie::gen_random_lines(FailingWriter, 2);
    assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe));
}

#[test]
fn library_can_run_repeatedly_without_initialization() -> io::Result<()> {
    for lines in [0, 1, 3] {
        let mut output = Vec::new();
        biggie::gen_random_lines(&mut output, lines)?;
        assert_eq!(
            String::from_utf8(output)
                .map_err(io::Error::other)?
                .lines()
                .count(),
            usize::try_from(lines).map_err(io::Error::other)?
        );
    }
    Ok(())
}

struct FailsAfterPrefix {
    accepted: Vec<u8>,
}

impl Write for FailsAfterPrefix {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let count = bytes.len().min(3_usize.saturating_sub(self.accepted.len()));
        if count == 0 {
            return Err(io::ErrorKind::BrokenPipe.into());
        }
        self.accepted.extend(bytes.iter().take(count).copied());
        Ok(count)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn partial_write_preserves_bytes_and_does_not_report_completed_generation()
-> Result<(), Box<dyn std::error::Error>> {
    use tracing::level_filters::LevelFilter;
    use tracing_subscriber::{fmt::format::FmtSpan, util::SubscriberInitExt};

    let dir = assert_fs::TempDir::new()?;
    let log = dir.path().join("events.jsonl");
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(LevelFilter::TRACE)
        .with_span_events(FmtSpan::CLOSE)
        .json()
        .with_writer(std::fs::File::create(&log)?)
        .finish();
    let mut writer = FailsAfterPrefix {
        accepted: Vec::new(),
    };
    let guard = subscriber.set_default();
    let result = biggie::gen_random_lines(&mut writer, 2);
    drop(guard);
    assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe));
    assert_eq!(writer.accepted.len(), 3);
    let records: Vec<serde_json::Value> = std::fs::read_to_string(log)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert!(records.iter().any(
        |record| record["span"]["name"] == "generate" && record["fields"]["message"] == "close"
    ));
    for record in records {
        assert!(record.pointer("/fields/lines_written").is_none());
        assert!(record.pointer("/span/lines_written").is_none());
        assert_ne!(
            record.pointer("/fields/message"),
            Some(&serde_json::json!("generation complete"))
        );
    }
    Ok(())
}

#[test]
fn filtered_generation_does_not_update_the_callers_parent_span()
-> Result<(), Box<dyn std::error::Error>> {
    use tracing::level_filters::LevelFilter;
    use tracing_subscriber::fmt::format::FmtSpan;

    let dir = assert_fs::TempDir::new()?;
    let log = dir.path().join("host.jsonl");
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(LevelFilter::INFO)
        .with_span_events(FmtSpan::CLOSE)
        .json()
        .with_writer(std::fs::File::create(&log)?)
        .finish();
    tracing::subscriber::with_default(subscriber, || {
        let _parent = tracing::info_span!("host", lines_written = 99).entered();
        biggie::gen_random_lines(Vec::new(), 2)
    })?;
    let records: Vec<serde_json::Value> = std::fs::read_to_string(log)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert_eq!(records.len(), 1);
    for record in records {
        assert_eq!(record["span"]["name"], "host");
        assert_eq!(record["span"]["lines_written"], 99);
    }
    Ok(())
}
