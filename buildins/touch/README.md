# touch

`touch` creates empty files and updates access and modification times without
changing existing contents. It supports BSD and GNU options on macOS and Linux.
This package replaces `touchr`; build and invoke it as `touch`.

From the workspace root:

```sh
cargo run -p touch -- example.txt
cargo run -p touch -- -m -t 202601021234.56 example.txt
cargo run -p touch -- --help
```

The first command creates `example.txt` if needed and sets both times to now.
The second changes only its modification time, using the local time zone.

## Options

| Option | Effect |
| --- | --- |
| `-a` | Change access time only. |
| `-m` | Change modification time only. Combine with `-a` for both. |
| `-c`, `--no-create` | Skip missing files. |
| `-h`, `--no-dereference` | Update symbolic links themselves, including dangling links; implies `-c`. |
| `-r FILE`, `--reference=FILE` | Copy the reference file's access and modification times separately. |
| `-t TIME` | Set `[[CC]YY]MMDDhhmm[.SS]` in local time. Omitted year means this year; two-digit years `69-99` mean `1969-1999`, and `00-68` mean `2000-2068`. Omitted seconds mean zero. |
| `-d DATE`, `--date=DATE` | Parse a BSD date/time or GNU expression, including relative times, offsets and `@SECONDS` since the Unix epoch. |
| `-A OFFSET` | Adjust times by `[-][[hh]mm]SS`; implies `-c`. For example, `-A -0130` subtracts 90 seconds. |
| `--time=WORD` | Select access time with `access`, `atime`, or `use`; modification time with `modify` or `mtime`. |
| `-f` | Accepted and ignored. |
| `--help` | Show usage and options. |
| `-V`, `--version` | Show the version. |

`TZ` selects the time zone; the system zone is used when it is unset. BSD dates
accept `YYYY-MM-DDThh:mm:SS[.fraction][Z]`, with a space instead of `T` and a comma
instead of the fractional period also accepted. `Z` selects UTC. Filesystem
precision determines which fractional digits can be retained.

To set timestamps relative to a reference file, combine `-r` and `-d`:

```sh
cargo run -p touch -- -r example.txt -d '1 hour' copy.txt
```

Each time is calculated from the corresponding reference time. Without `-r`,
relative dates use the current time. A nonzero `-A` adjusts existing timestamps
unless `-r`, `-t`, or `-d` supplies a base. As on BSD, `-A 00` uses the current
time when no other source is given. Duration components can overflow their
usual clock ranges: `-A 60` adds one minute.

## Compatibility choices

The baseline is macOS/FreeBSD, with GNU extensions where they can coexist:

- `-` is a literal filename, not GNU's stdout-file operand. Use `--` before
  filenames beginning with an option prefix, or `./NAME` to disambiguate names.
- With at least two file operands and no explicit time source, an eight- or
  ten-digit first operand is the BSD legacy `MMDDhhmm[YY]` timestamp. Its
  two-digit year cutoff is `39`, unlike `-t`'s `69`, and its seconds come from
  the current clock. A nonzero `-A` consumes this operand but adjusts the file's
  existing times.
- Repeated options use their last value. `-t` replaces earlier `-r`/`-d` sources;
  a later `-r` or `-d` replaces `-t`. The `-r` plus `-d` combination deliberately
  follows GNU and adjusts the reference timestamps, regardless of their order.
- With `-h -r SYMLINK`, reference times come from the symlink itself, as in GNU;
  BSD would follow the reference link. Destination links are not followed.
- Invalid calendar dates and nonexistent local times during daylight-saving
  transitions are rejected rather than normalized as BSD does. An ambiguous
  numeric local time uses its first occurrence.
- The old `touchr` format with seconds appended without a period is no longer
  accepted. Use `202601021234.56`, not `20260102123456`.

The CLI reads no stdin and is silent on success. Errors go to stderr; processing
continues with later files after a file error. Exit status is `0` on success,
`1` for date/reference/filesystem failures, and `2` for argument errors.
Creation uses ordinary permissions subject to the process umask. Directories,
FIFOs and files that cannot be opened for reading can still have their times
updated when permissions allow it. The command does not set birth or status-change
times. GNU date parsing is provided by `parse_datetime`; it is not a promise of
byte-for-byte GNU diagnostics or every historical date grammar.

References: [FreeBSD touch](https://man.freebsd.org/cgi/man.cgi?query=touch&sektion=1)
and [GNU touch](https://www.gnu.org/software/coreutils/manual/html_node/touch-invocation.html).

[Workspace README](../../README.md)
