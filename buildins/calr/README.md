# calr

`calr` prints a month or year as a Sunday-first text calendar.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `YEAR` | Optional year, 1-9999; shows the whole year unless `-m` selects a month. |
| `-m MONTH` | Month number or unambiguous English name prefix; uses the current year if YEAR is omitted. |
| `-y, --year` | Show the whole current year; conflicts with YEAR and `-m`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

With no arguments, show the current local month and highlight today.

## Before using it

Reference: [util-linux cal(1)](https://man7.org/linux/man-pages/man1/cal.1.html).
The argument layout is different: the month is an option, not a positional value.
Calendars use proleptic Gregorian dates and fixed English labels; there is no
historical calendar-reform switch, Monday-first option, or week-number display.
Today's highlight uses ANSI escapes even when output is redirected.

## Test notes

Fixed-date fixtures use BSD `cal` layout. No-argument output and `--year`
depend on the current local date, and today's highlight adds ANSI escapes.
Keep those assertions date-aware; use explicit months and years for stable
calendar fixtures.

[All commands](../../README.md)
