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

#[test]
fn custom_text_units_validation_and_buffer_boundaries() -> anyhow::Result<()> {
    use biggie::{Alphabet, LengthUnit, TextOptions, generate_text};
    let options = TextOptions {
        generation: GenerationOptions {
            lines: 1,
            words_per_line: 1..=1,
            word_length: 8193..=8193,
            final_newline: false,
            ..Default::default()
        },
        alphabet: Alphabet::Custom(vec!['猫']),
        length_unit: LengthUnit::Scalars,
        separator: "\u{2003}".into(),
    };
    let mut bytes = Vec::new();
    generate_text(&mut bytes, &options)?;
    assert_eq!(bytes, "猫".repeat(8193).as_bytes());
    assert!(generate_text(FailingWriter, &options).is_err_and(|e| {
        e.downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe)
    }));
    for alphabet in [
        Alphabet::Custom(vec![]),
        Alphabet::Custom(vec!['a', 'a']),
        Alphabet::Custom(vec!['\n']),
        Alphabet::Custom(vec!['a'; 4097]),
    ] {
        let mut out = b"keep".to_vec();
        let invalid = TextOptions {
            alphabet,
            generation: GenerationOptions {
                lines: 0,
                ..Default::default()
            },
            ..options.clone()
        };
        assert!(generate_text(&mut out, &invalid).is_err_and(|e| {
            e.downcast_ref::<io::Error>()
                .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput)
        }));
        assert_eq!(out, b"keep");
    }
    let mut ascii = Vec::new();
    generate_text(
        &mut ascii,
        &TextOptions {
            generation: GenerationOptions {
                lines: 2,
                seed: Some(42),
                words_per_line: 2..=2,
                word_length: 3..=3,
                ..Default::default()
            },
            ..Default::default()
        },
    )?;
    // Captured from b3b80d2 with the locked rand implementation on macOS arm64.
    assert_eq!(ascii, b"Pi3 ZCn\nvL2 IeA\n");
    Ok(())
}

#[test]
fn records_preserve_schedule_boundaries_and_validate_direct_calls() -> anyhow::Result<()> {
    use biggie::{RecordOptions, generate_records};
    let options = RecordOptions {
        records: vec!["a".into(), "b".into(), "a".into()],
        cycles: 2,
        ..Default::default()
    };
    let mut bytes = Vec::new();
    generate_records(&mut bytes, &options)?;
    assert_eq!(bytes, b"a\nb\na\na\nb\na\n");
    let mut short = FailsAfterPrefix {
        accepted: Vec::new(),
    };
    assert!(generate_records(&mut short, &options).is_err_and(|e| {
        e.downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe)
    }));
    assert_eq!(short.accepted, b"a\nb");
    generate_records(
        FailingWriter,
        &RecordOptions {
            records: vec![String::new()],
            final_newline: false,
            ..Default::default()
        },
    )?;
    generate_records(
        FailingWriter,
        &RecordOptions {
            cycles: u64::MAX,
            ..Default::default()
        },
    )?;
    for invalid in [
        RecordOptions {
            repeat: 0,
            ..options.clone()
        },
        RecordOptions {
            repeat: u64::MAX,
            ..options.clone()
        },
        RecordOptions {
            records: vec!["x\ny".into()],
            ..options.clone()
        },
        RecordOptions {
            records: vec![String::new(); 100_001],
            ..options.clone()
        },
        RecordOptions {
            records: vec!["x".repeat(8 * 1024 * 1024 + 1)],
            ..options.clone()
        },
    ] {
        let mut bytes = b"keep".to_vec();
        assert!(generate_records(&mut bytes, &invalid).is_err_and(|e| {
            e.downcast_ref::<io::Error>()
                .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput)
        }));
        assert_eq!(bytes, b"keep");
    }
    Ok(())
}

#[cfg(feature = "csv")]
#[test]
fn field_library_preserves_csv_content() -> anyhow::Result<()> {
    use biggie::{FieldFormat, FieldOptions, generate_fields};
    let options = FieldOptions {
        lines: 2,
        fields: 3,
        format: FieldFormat::Csv,
        delimiter: b',',
        values: vec!["a,b".into(), String::new(), "a\"b\n".into()],
        final_newline: false,
        ..Default::default()
    };
    let mut bytes = Vec::new();
    generate_fields(&mut bytes, &options)?;
    assert_eq!(bytes, b"\"a,b\",,\"a\"\"b\n\"\n\"a,b\",,\"a\"\"b\n\"");
    let rows: Vec<_> = csv::ReaderBuilder::new()
        .has_headers(false)
        .from_reader(bytes.as_slice())
        .records()
        .collect::<Result<_, _>>()
        .map_err(io::Error::other)?;
    assert_eq!(rows.len(), 2);
    for row in rows {
        assert_eq!(row.iter().collect::<Vec<_>>(), vec!["a,b", "", "a\"b\n"]);
    }
    assert!(generate_fields(FailingWriter, &options).is_err_and(|e| {
        e.downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe)
    }));
    Ok(())
}

#[test]
fn field_library_validates_options_and_repeats_seeded_output() -> anyhow::Result<()> {
    use biggie::{FieldOptions, generate_fields};
    let options = FieldOptions {
        lines: 2,
        values: vec!["a".into(), String::new(), "c".into()],
        ..Default::default()
    };
    for invalid in [
        FieldOptions {
            fields: 0,
            ..options.clone()
        },
        FieldOptions {
            delimiter: b'\n',
            ..options.clone()
        },
        FieldOptions {
            empty_every: Some(0),
            ..options.clone()
        },
        FieldOptions {
            values: vec!["embedded\nnewline".into()],
            ..options.clone()
        },
        FieldOptions {
            seed: Some(1),
            ..options.clone()
        },
        FieldOptions {
            fields: u32::MAX,
            ..options
        },
    ] {
        let mut out = b"keep".to_vec();
        assert!(generate_fields(&mut out, &invalid).is_err_and(|e| {
            e.downcast_ref::<io::Error>()
                .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput)
        }));
        assert_eq!(out, b"keep");
    }
    let seeded = FieldOptions {
        lines: 3,
        seed: Some(42),
        ..Default::default()
    };
    let mut first = Vec::new();
    let mut second = Vec::new();
    generate_fields(&mut first, &seeded)?;
    generate_fields(&mut second, &seeded)?;
    assert_eq!(first, second);
    Ok(())
}

#[test]
fn byte_budgets_cross_buffer_boundaries_and_propagate_failures() -> anyhow::Result<()> {
    use biggie::{ByteOptions, ByteSource, generate_bytes};
    for bytes in [0, 1, 8191, 8192, 8193, 32769] {
        let options = ByteOptions {
            bytes,
            source: ByteSource::Pattern(vec![0, 255, 27, 13, 10]),
        };
        let mut out = Vec::new();
        generate_bytes(&mut out, &options)?;
        assert_eq!(out.len(), usize::try_from(bytes).map_err(io::Error::other)?);
        assert!(
            out.as_chunks::<5>()
                .0
                .iter()
                .all(|chunk| *chunk == [0, 255, 27, 13, 10])
        );
        assert_eq!(
            out.as_chunks::<5>().1,
            [0, 255, 27, 13, 10]
                .get(..out.len() % 5)
                .ok_or_else(|| io::Error::other("remainder"))?
        );
    }
    let options = ByteOptions {
        bytes: 8193,
        source: ByteSource::Random { seed: Some(42) },
    };
    let mut first = Vec::new();
    let mut second = Vec::new();
    generate_bytes(&mut first, &options)?;
    generate_bytes(&mut second, &options)?;
    assert_eq!(first, second);
    second.clear();
    generate_bytes(
        &mut second,
        &ByteOptions {
            source: ByteSource::Random { seed: Some(43) },
            ..options.clone()
        },
    )?;
    assert_ne!(first, second);
    let mut writer = FailsAfterPrefix {
        accepted: Vec::new(),
    };
    assert!(
        generate_bytes(
            &mut writer,
            &ByteOptions {
                bytes: 5,
                source: ByteSource::Pattern(b"abcde".to_vec())
            }
        )
        .is_err_and(|e| e
            .downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe))
    );
    assert_eq!(writer.accepted, b"abc");
    for pattern in [Vec::new(), vec![0; 8 * 1024 * 1024 + 1]] {
        let mut out = b"keep".to_vec();
        assert!(
            generate_bytes(
                &mut out,
                &ByteOptions {
                    bytes: 0,
                    source: ByteSource::Pattern(pattern)
                }
            )
            .is_err_and(|e| e
                .downcast_ref::<io::Error>()
                .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput))
        );
        assert_eq!(out, b"keep");
    }
    Ok(())
}

#[test]
fn pair_library_checks_multisets_endings_and_failures() -> anyhow::Result<()> {
    use biggie::{PairOptions, generate_pair};
    let options = PairOptions {
        left_only: 2,
        shared: 3,
        right_only: 1,
        copies: 2,
        ..Default::default()
    };
    let mut left = Vec::new();
    let mut right = Vec::new();
    generate_pair(&mut left, &mut right, &options)?;
    let left: Vec<_> = left
        .split(|b| *b == b'\n')
        .filter(|r| !r.is_empty())
        .collect();
    let right: Vec<_> = right
        .split(|b| *b == b'\n')
        .filter(|r| !r.is_empty())
        .collect();
    assert_eq!(left.len(), 10);
    assert_eq!(right.len(), 8);
    assert!(left.windows(2).all(|w| matches!(w,[a,b] if a<=b)));
    assert!(right.windows(2).all(|w| matches!(w,[a,b] if a<=b)));
    assert_eq!(left.iter().filter(|r| !right.contains(r)).count(), 4);
    assert_eq!(right.iter().filter(|r| !left.contains(r)).count(), 2);
    assert_eq!(left.iter().filter(|r| right.contains(r)).count(), 6);
    let options = PairOptions {
        shared: 1,
        copies: 2,
        line_ending: LineEnding::CrLf,
        final_newline: false,
        ..Default::default()
    };
    let mut left = Vec::new();
    let mut right = Vec::new();
    generate_pair(&mut left, &mut right, &options)?;
    assert_eq!(left, b"0000000000000000\r\n0000000000000000");
    assert_eq!(left, right);
    assert!(
        generate_pair(FailingWriter, Vec::new(), &options).is_err_and(|e| e
            .downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe))
    );
    assert!(
        generate_pair(Vec::new(), FailingWriter, &options).is_err_and(|e| e
            .downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::BrokenPipe))
    );
    generate_pair(FailingWriter, FailingWriter, &PairOptions::default())?;
    for invalid in [
        PairOptions {
            left_only: u64::MAX,
            shared: 1,
            ..Default::default()
        },
        PairOptions {
            left_only: u64::MAX,
            copies: 2,
            ..Default::default()
        },
        PairOptions {
            copies: 0,
            ..Default::default()
        },
    ] {
        let mut left = b"left".to_vec();
        let mut right = b"right".to_vec();
        assert!(
            generate_pair(&mut left, &mut right, &invalid).is_err_and(|e| e
                .downcast_ref::<io::Error>()
                .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput))
        );
        assert_eq!(left, b"left");
        assert_eq!(right, b"right");
    }
    Ok(())
}

struct ShortWriter {
    bytes: Vec<u8>,
}
impl Write for ShortWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let count = bytes.len().min(2);
        self.bytes.extend(bytes.iter().take(count));
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        Err(io::Error::other("caller owns flush"))
    }
}
#[test]
fn all_operations_handle_short_writes_without_flushing() -> anyhow::Result<()> {
    use biggie::{ByteOptions, ByteSource, FieldOptions, PairOptions, RecordOptions, TextOptions};
    let mut writer = ShortWriter { bytes: Vec::new() };
    biggie::generate_text(
        &mut writer,
        &TextOptions {
            generation: GenerationOptions {
                lines: 1,
                words_per_line: 0..=0,
                ..Default::default()
            },
            ..Default::default()
        },
    )?;
    biggie::generate_records(
        &mut writer,
        &RecordOptions {
            records: vec!["é猫".into()],
            ..Default::default()
        },
    )?;
    biggie::generate_fields(
        &mut writer,
        &FieldOptions {
            lines: 1,
            fields: 1,
            values: vec!["é猫".into()],
            ..Default::default()
        },
    )?;
    biggie::generate_bytes(
        &mut writer,
        &ByteOptions {
            bytes: 3,
            source: ByteSource::Pattern(vec![255]),
        },
    )?;
    assert_eq!(
        writer.bytes,
        ["\né猫\né猫\n".as_bytes(), &[255, 255, 255]].concat()
    );
    let mut other = ShortWriter { bytes: Vec::new() };
    biggie::generate_pair(
        &mut writer,
        &mut other,
        &PairOptions {
            right_only: 1,
            ..Default::default()
        },
    )?;
    assert_eq!(other.bytes, b"0000000000000000\n");
    Ok(())
}

#[test]
fn random_plain_fields_keep_the_requested_field_count() -> anyhow::Result<()> {
    let mut bytes = Vec::new();
    biggie::generate_fields(
        &mut bytes,
        &biggie::FieldOptions {
            lines: 1,
            fields: 3,
            delimiter: b'A',
            word_length: 100..=100,
            seed: Some(42),
            ..Default::default()
        },
    )?;
    assert_eq!(
        bytes
            .split(|b| *b == b'A')
            .map(<[u8]>::len)
            .collect::<Vec<_>>(),
        [100, 100, 101]
    );
    assert_eq!(bytes.len(), 303);
    Ok(())
}
#[cfg(feature = "csv")]
#[test]
fn csv_random_quote_overhead_is_included_in_row_limit() {
    let options = biggie::FieldOptions {
        lines: 0,
        format: biggie::FieldFormat::Csv,
        fields: 2,
        word_length: 4_194_302..=4_194_302,
        delimiter: b'A',
        seed: Some(42),
        ..Default::default()
    };
    let mut out = b"keep".to_vec();
    assert!(biggie::generate_fields(&mut out, &options).is_err_and(|e| {
        e.downcast_ref::<io::Error>()
            .is_some_and(|source| source.kind() == io::ErrorKind::InvalidInput)
    }));
    assert_eq!(out, b"keep");
}

#[test]
fn seeded_fields_preserve_delimiter_rejection_sequence() -> anyhow::Result<()> {
    let mut output = Vec::new();
    biggie::generate_fields(
        &mut output,
        &biggie::FieldOptions {
            lines: 2,
            fields: 3,
            word_length: 12..=12,
            delimiter: b'A',
            seed: Some(42),
            ..Default::default()
        },
    )?;
    // Captured before helper extraction with the locked rand implementation.
    assert_eq!(
        output,
        b"hPi3oZCnaWvLAoIe07mg3ZtJzA0NoKhdDqpQ2d\ngaDFWTcIylNhAKp3bM477b3ppAOWkYYmEGbCym\n"
    );
    Ok(())
}
