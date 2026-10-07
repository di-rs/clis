use cli_bench as bench_api;
use cli_bench::*;
#[path = "common/rss.rs"]
mod rss_support;
#[path = "common/production_validation.rs"]
mod validation_support;
use validation_support::{Fixture, TestResult};

#[test]
fn public_rss_workflow_retains_separate_memory_size_and_final_evidence() -> TestResult {
    let fixture = Fixture::new("printf 'EFGH\\n'", "printf 'EFGH\\n'")?;
    let tool = rss_support::time_tool(&fixture, Platform::Darwin, "")?;
    let mut writer = fixture.store.begin_run(&fixture.suite)?;
    let validated = validate_experiment(
        fixture.prepare(MeasurementProfile::Smoke)?,
        &mut writer,
        &fixture.runner,
    )?;
    let samples = measure_rss(
        &validated,
        &mut writer,
        &fixture.runner,
        &tool,
        Platform::Darwin,
    )?;
    require(samples.len() == 2, "wrong smoke sample count")?;
    for sample in samples {
        require(
            sample.rss.bytes == 1024 && sample.status == 0,
            "wrong RSS/status",
        )?;
        require(
            sample.raw_stdout.is_none(),
            "drained sink claimed captured stdout",
        )?;
        for path in [sample.raw_resource, sample.raw_stderr, sample.target_stderr] {
            require(
                writer.path().join(path).is_file(),
                "raw sample evidence missing",
            )?;
        }
    }
    for path in [
        "raw/rss/samples.json",
        "raw/rss/executable-sizes.json",
        "raw/rss/tool.json",
        "raw/rss/final-last-line/report.json",
        "measurement-lock.json",
    ] {
        require(writer.path().join(path).is_file(), "final evidence missing")?;
    }
    require(
        std::fs::read_dir(writer.path().join("raw/timing"))?
            .next()
            .is_none(),
        "RSS created timing observations",
    )?;
    Ok(())
}

#[test]
#[ignore = "opt-in native time adapter; set CLI_BENCH_TIME explicitly"]
fn native_time_adapter_contracts_plumbing_only() -> TestResult {
    let platform = if cfg!(target_os = "macos") {
        Platform::Darwin
    } else if cfg!(target_os = "linux") {
        Platform::Linux
    } else {
        return Err("native RSS requires Linux or macOS".into());
    };
    let path = std::fs::canonicalize(
        std::env::var_os("CLI_BENCH_TIME").ok_or("set CLI_BENCH_TIME explicitly")?,
    )?;
    let tool = BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&path)?,
            version: format!("explicit native {platform:?} time adapter"),
        },
        path,
    };
    for mode in ["zero", "one", "diagnostic", "pipe", "file", "signal"] {
        let mut fixture = native_fixture(mode)?;
        fixture.root = fixture.root.into_persistent();
        eprintln!(
            "plumbing-only native RSS {mode} evidence: {}",
            fixture.root.display()
        );
        let mut writer = fixture.store.begin_run(&fixture.suite)?;
        let validated = validate_experiment(
            fixture.prepare(MeasurementProfile::Smoke)?,
            &mut writer,
            &fixture.runner,
        )?;
        let result = measure_rss(&validated, &mut writer, &fixture.runner, &tool, platform);
        if mode == "signal" {
            require(
                result.is_err(),
                "signal accepted as normal expected exit 143",
            )?;
        } else {
            let samples = result?;
            require(samples.len() == 2, "native smoke sample count wrong")?;
            for sample in samples {
                let raw = std::fs::read(writer.path().join(sample.raw_resource))?;
                require(
                    parse_peak_rss(platform, &raw)? == sample.rss,
                    "native parser observation mismatch",
                )?;
                require(
                    sample.invocation_scope == if mode == "pipe" { "Pipeline" } else { "Direct" },
                    "RSS invocation scope changed",
                )?;
            }
        }
    }
    Ok(())
}
fn native_fixture(mode: &str) -> Result<Fixture, Box<dyn std::error::Error>> {
    let body = match mode {
        "one" => "printf 'EFGH\\n'; exit 1",
        "diagnostic" => "printf 'EFGH\\n'; printf 'target diagnostic without newline' >&2",
        "pipe" => "test -p /dev/stdin || exit 9; test -p /dev/stdout || exit 8; /bin/cat",
        "file" => "test -f /dev/stdin || exit 9; /bin/cat",
        "signal" => {
            r#"n=0
if [ -f "$HOME/signal-count" ]; then read -r n < "$HOME/signal-count"; fi
n=$((n+1)); printf '%s\n' "$n" > "$HOME/signal-count"
printf 'EFGH\n'
if [ "$n" -le 2 ]; then exit 143; fi
kill -TERM "$$""#
        }
        _ => "printf 'EFGH\\n'",
    };
    let previous = if mode == "signal" {
        "printf 'EFGH\\n'; exit 143"
    } else {
        body
    };
    let mut fixture = Fixture::new(body, previous)?;
    let case = fixture.suite.cases.first_mut().ok_or("case")?;
    if mode == "one" {
        case.expected_status = 1;
    }
    if mode == "signal" {
        case.expected_status = 143;
    }
    if mode == "diagnostic" {
        case.correctness = vec![
            CorrectnessRule::Literal {
                stream: Stream::Stdout,
                text: "EFGH\n".into(),
            },
            CorrectnessRule::Literal {
                stream: Stream::Stderr,
                text: "target diagnostic without newline".into(),
            },
        ];
    }
    if matches!(mode, "pipe" | "file") {
        case.argv.clear();
        case.io.stdin = if mode == "pipe" {
            StdinPolicy::Pipe {
                dataset: "tiny".into(),
            }
        } else {
            StdinPolicy::RegularFile {
                dataset: "tiny".into(),
            }
        };
        if mode == "file" {
            case.io.stdout = StdoutPolicy::ScratchFile {
                path: "output".into(),
            };
        }
        case.correctness = vec![
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "414243440a454647480a".into(),
            },
            CorrectnessRule::EmptyStderr {},
        ];
        if mode == "pipe" {
            let bound = |path: &str| -> Result<BoundTool, BenchError> {
                Ok(BoundTool {
                    path: path.into(),
                    identity: ToolIdentity {
                        file: fingerprint(std::path::Path::new(path))?,
                        version: "native pipe plumbing tool".into(),
                    },
                })
            };
            fixture.request.pipeline = Some(PipelineTools {
                bash: bound("/bin/bash")?,
                cat: bound("/bin/cat")?,
            });
        }
    }
    Ok(fixture)
}
fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
