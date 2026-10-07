use super::bench_api as cli_bench;
use super::validation_support::{self, Fixture};
use cli_bench::*;

pub fn time_tool(
    fixture: &Fixture,
    platform: Platform,
    mode: &str,
) -> Result<BoundTool, BenchError> {
    let body = match platform {
        Platform::Linux => format!(
            r#"test "$1" = '-v' && test "$2" = '-o' || exit 9
output=$3; shift 4
"$@"
status=$?
{mode}
printf '\tMaximum resident set size (kbytes): 1024\n\tExit status: %s\n' "$status" > "$output"
exit "$status""#
        ),
        Platform::Darwin => format!(
            r#"test "$1" = '-l' || exit 9
shift
"$@"
status=$?
{mode}
printf '        0.00 real         0.00 user         0.00 sys\n             1024  maximum resident set size\n' >&2
exit "$status""#
        ),
    };
    let path = validation_support::script(fixture.root.path(), "time-tool", &body)?;
    Ok(BoundTool {
        identity: ToolIdentity {
            file: fingerprint(&path)?,
            version: "original time plumbing fixture".into(),
        },
        path,
    })
}
