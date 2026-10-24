use itertools::izip;
use std::io::IsTerminal;
use std::io::{BufWriter, Write};

mod cli;
use crate::cli::{Cli, CliError};

use calr::{CalendarDate, Month, format_month_with_highlight, get_today};

fn main() -> std::process::ExitCode {
    if let Some(status) = cli_schema::emit::<Cli>() {
        return status;
    }
    cli_log::run("calr", |mut cli: Cli| {
        cli.setup_default();
        match run(&cli) {
            Ok(()) => exitcode::OK,
            Err(e) => {
                cli_log::diagnostic!("Error: {e}");
                exitcode::DATAERR
            }
        }
    })
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();
    let year = cli.get_year();
    let today = get_today();
    let highlight = std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();

    if let Some(month) = cli.month {
        let date = CalendarDate::new(year, month, today);
        let lines = format_month_with_highlight(&date, true, highlight);
        writeln!(writer, "{}", lines.join("\n"))?;
    } else {
        let months: Vec<_> = (1..=12)
            .map(|month| {
                let date = CalendarDate::new(year, Month::new(month), today);
                format_month_with_highlight(&date, false, highlight)
            })
            .collect();

        writeln!(writer, "{year:>32}")?;

        for (i, chunk) in months.chunks(3).enumerate() {
            if let [m1, m2, m3] = chunk {
                for lines in izip!(m1, m2, m3) {
                    writeln!(writer, "{}{}{}", lines.0, lines.1, lines.2)?;
                }
                if i < 3 {
                    writeln!(writer)?;
                }
            }
        }
    }

    writer.flush()?;
    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
