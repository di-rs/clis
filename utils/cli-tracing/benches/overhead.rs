use clap::Parser;
use std::{hint::black_box, process::ExitCode, time::Instant};

const ITERATIONS: u64 = 100_000;

#[derive(Parser)]
struct Cli {
    #[command(flatten)]
    logging: cli_tracing::LogArgs,
    /// Measure the same checksum without stage instrumentation
    #[arg(long)]
    bare: bool,
    // Cargo passes this to harness=false benchmark executables.
    #[arg(long, hide = true)]
    bench: bool,
}

fn checksum(input: &[u8]) -> u64 {
    input
        .iter()
        .fold(0_u64, |sum, byte| sum.wrapping_add(u64::from(*byte)))
}

#[tracing::instrument(level = "debug", skip_all, fields(bytes = input.len()))]
fn instrumented(input: &[u8]) -> u64 {
    checksum(input)
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    cli_tracing::run::<Cli>(&cli.logging, || {
        let input = black_box([7_u8; 4096]);
        println!("mode,repetition,operations,elapsed_ns,checksum");
        for repetition in 0_u64..9 {
            let started = Instant::now();
            let mut result = 0_u64;
            for _ in 0..ITERATIONS {
                let value = if cli.bare {
                    checksum(black_box(&input))
                } else {
                    instrumented(black_box(&input))
                };
                result = result.wrapping_add(black_box(value));
            }
            let elapsed = started.elapsed();
            // Independent expected value: 4096 bytes, each 7, for every invocation.
            anyhow::ensure!(result == 2_867_200_000, "incorrect checksum: {result}");
            if repetition >= 3 {
                println!(
                    "{},{},{ITERATIONS},{},{result}",
                    if cli.bare { "bare" } else { "instrumented" },
                    repetition.saturating_sub(3),
                    elapsed.as_nanos()
                );
            }
        }
        Ok(ExitCode::SUCCESS)
    })
}
