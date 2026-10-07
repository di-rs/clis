use super::bench_api as cli_bench;
use super::validation_support::{self, Fixture};
use cli_bench::*;

pub fn engine(fixture: &Fixture, mode: &str) -> Result<BoundTool, BenchError> {
    let (before, after) = match mode {
        "force-zero" => ("", "status=0"),
        "wrong-command" => ("", "command=/wrong/command"),
        _ => (mode, ""),
    };
    let version = if mode == "observed-version" {
        "hyperfine 1.19.0"
    } else {
        "hyperfine 1.20.0"
    };
    let body = format!(
        r#"
if [ "$1" = '--version' ]; then printf '{version}\n'; exit 0; fi
output=''
while [ "$#" -gt 0 ]; do
    if [ "$1" = '--export-json' ]; then shift; output=$1; fi
    command=$1
    shift
done
{before}
/bin/sh -c "$command"
status=$?
{after}
printf '{{"results":[{{"command":"%s","times":[0.001],"exit_codes":[%s]}}]}}\n' "$command" "$status" > "$output"
printf 'fixture engine warning\n' >&2
"#
    );
    let path = validation_support::script(fixture.root.path(), "engine", &body)?;
    Ok(BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&path)?,
            version: "hyperfine 1.20.0".into(),
        },
        path,
    })
}

pub fn prepare_smoke<'a>(
    fixture: &'a Fixture,
    writer: &mut RunWriter,
) -> Result<ValidatedExperiment<'a>, BenchError> {
    let prepared = fixture.prepare(MeasurementProfile::Smoke)?;
    validate_experiment(prepared, writer, &fixture.runner)
}
