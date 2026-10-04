# calr

`calr` prints a month or year as a Sunday-first text calendar.

## Supported capabilities

- With no arguments, show the current local month and highlight today.
- A positional year selects the whole year (1–9999); `-y`/`--year` shows the current year.
- `-m` selects a month by number or an unambiguous English name prefix.
- Combine `-m` with a positional year to select a particular month and year.

## Examples

From the workspace root:

```sh
cargo run -p calr -- -m 2 2024
cargo run -p calr -- 2024
```

## Differences and limits

Reference: [util-linux cal(1)](https://man7.org/linux/man-pages/man1/cal.1.html).
The argument layout is different: the month is an option, not a positional value.
Calendars use proleptic Gregorian dates and fixed English labels; there is no
historical calendar-reform switch, Monday-first option, or week-number display.
Today's highlight uses ANSI escapes even when output is redirected.

[Workspace README](../README.md)
