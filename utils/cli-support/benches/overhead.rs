use cli_support::{Config, Format, Runtime};
use std::{hint::black_box, io, time::Instant};
use tracing::dispatcher::with_default;

const ITERATIONS: u64 = 100_000;

fn checksum(input: &[u8]) -> u64 {
    input
        .iter()
        .fold(0_u64, |sum, byte| sum.wrapping_add(u64::from(*byte)))
}

fn instrumented(input: &[u8]) -> u64 {
    let _stage =
        tracing::info_span!(target: "clis::timing", "checksum", bytes = input.len()).entered();
    checksum(input)
}

fn sample(mode: &str, repetition: u64, mut work: impl FnMut() -> u64) {
    let started = Instant::now();
    let mut result = 0_u64;
    for _ in 0..ITERATIONS {
        result = result.wrapping_add(black_box(work()));
    }
    let elapsed = started.elapsed();
    // An independent expected value: 4096 bytes, each equal to 7, per invocation.
    assert_eq!(result, 2_867_200_000);
    if repetition >= 3 {
        println!(
            "{mode},{},{ITERATIONS},{},{result}",
            repetition.saturating_sub(3),
            elapsed.as_nanos()
        );
    }
}

fn main() -> io::Result<()> {
    let input = black_box([7_u8; 4096]);
    let disabled = Runtime::with_writer(&Config::default(), io::sink());
    let enabled = Runtime::with_writer(
        &Config {
            timings: true,
            format: Format::Json,
            ..Config::default()
        },
        io::sink(),
    );
    println!("mode,repetition,operations,elapsed_ns,checksum");
    // Three warm-up rounds, then six reported rounds with rotated order.
    for repetition in 0_u64..9 {
        for offset in 0..3 {
            match repetition.wrapping_add(offset) % 3 {
                0 => sample("bare", repetition, || checksum(black_box(&input))),
                1 => with_default(disabled.dispatch(), || {
                    sample("disabled", repetition, || instrumented(black_box(&input)));
                }),
                _ => with_default(enabled.dispatch(), || {
                    sample("timings_json_sink", repetition, || {
                        instrumented(black_box(&input))
                    });
                }),
            }
        }
    }
    disabled.finish()?;
    enabled.finish()
}
