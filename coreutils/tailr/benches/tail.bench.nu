#!/usr/bin/env nu
# Forward the documented Python runner arguments, including absolute binaries.
def --wrapped main [...args: string] {
    let root = ($env.FILE_PWD | path join "../../.." | path expand)
    ^python3 ($root | path join "scripts/benchmark_tail.py") ...$args
    if $env.LAST_EXIT_CODE != 0 { exit $env.LAST_EXIT_CODE }
}
