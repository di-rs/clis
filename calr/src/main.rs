use clap::Parser;
use itertools::izip;
use std::io::{BufWriter, Write};

mod cli;
use crate::cli::{Cli, CliError};

use calr::{CalendarDate, Month, format_month, get_today};

fn main() {
    let mut cli = Cli::parse();
    cli.setup_default();

    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let mut writer = get_writer();
    let year = cli.get_year();
    let today = get_today();

    if let Some(month) = cli.month {
        let date = CalendarDate::new(year, month, today);
        let lines = format_month(&date, true);
        writeln!(writer, "{}", lines.join("\n"))?;
    } else {
        let months: Vec<_> = (1..=12)
            .map(|month| {
                let date = CalendarDate::new(year, Month::new(month), today);
                format_month(&date, false)
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

    Ok(())
}

fn get_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}
