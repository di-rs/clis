# touch

`touch` creates empty files and updates access and modification times without
changing existing contents.

From the workspace root:

```sh
cargo run -p touch -- example.txt
cargo run -p touch -- -m -t 202601021234.56 example.txt
cargo run -p touch -- -r example.txt -d '1 hour' copy.txt
```

These commands set both times to now, set only the modification time to
January 2, 2026 at 12:34:56, and copy each reference time plus one hour.

## Options

| Option | Effect |
| --- | --- |
| `-a` / `-m` | Change only access / modification time. Combine for both. |
| `-c`, `--no-create` | Skip missing files. |
| `-h`, `--no-dereference` | Update symlinks themselves; skip missing files. Also read reference symlinks themselves. |
| `-r FILE`, `--reference=FILE` | Copy each timestamp from `FILE`. Combine with `-d` to adjust it. |
| `-t TIME` | Set local time as `[[CC]YY]MMDDhhmm[.SS]`, for example `202601021234.56`. Omitted year means this year; omitted seconds mean zero. |
| `-d DATE`, `--date=DATE` | Set a date/time such as `2026-01-02 12:34:56`, a relative time such as `1 hour ago`, or epoch seconds such as `@946684800`. |
| `-A OFFSET` | Adjust existing times by `[-][[hh]mm]SS`; `-0130` subtracts 90 seconds. Skip missing files. |
| `--time=WORD` | Select `access`, `atime`, or `use` for access time; `modify` or `mtime` for modification time. |
| `-f` | Accepted and ignored. |
| `--help` / `--version` | Show help / version. |

Dates use the local zone, configurable with `TZ`; append `Z` for UTC or an offset
such as `+02:00`. Fractional seconds are supported by `-d`. Invalid dates and
nonexistent local times are rejected. Two-digit years with `-t` use 1969-2068.

Use `--` before filenames beginning with `-`; a lone `-` is a literal filename.
Prefix numeric filenames with `./` to avoid the positional `MMDDhhmm[YY]` time
syntax, whose year range is 1939-2038 and whose seconds come from the current clock.
Repeated options use the last value; `-t` replaces earlier `-r`/`-d` sources and
vice versa. A nonzero `-A` adjusts an explicit time source or the existing file's
times; `-A 00` without a time source sets times to now.

Success is silent. Errors go to stderr, and later files are still processed.
Exit status is `0` on success, `1` for date or filesystem errors, and `2` for
argument errors. Timestamp precision depends on the filesystem; birth and
status-change times cannot be set.

[Workspace README](../../README.md)
