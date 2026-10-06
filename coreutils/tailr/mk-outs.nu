#!/usr/bin/env nu
# Capture reviewed reference/candidate evidence; never delete expected fixtures.
def main [--reference: path, --candidate: path, --output: path] {
    if $reference == null or $candidate == null or $output == null {
        error make {msg: "Pass --reference /absolute/GNU/tail --candidate /absolute/tailr --output /empty/capture/dir"}
    }
    let root = ($env.FILE_PWD | path join "../.." | path expand)
    ^python3 ($root | path join "scripts/reference_check.py") --reference $reference --candidate $candidate --cases ($env.FILE_PWD | path join "tests/reference_cases.json") --output-dir $output
    if $env.LAST_EXIT_CODE != 0 { exit $env.LAST_EXIT_CODE }
}
