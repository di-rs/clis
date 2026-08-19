use biggie::gen_random_lines;
use clap::Parser;
use std::{
    fs::File,
    io::{BufWriter, Write},
    path::Path,
};
use thousands::Separable;

mod cli;
use crate::cli::{Cli, CliError};

fn main() {
    let cli = Cli::parse();
    let result = run(&cli);

    match result {
        Ok(()) => {
            std::process::exit(exitcode::OK);
        }
        Err(CliError::FileCreate(_, e)) => {
            eprintln!("{e}");
            std::process::exit(exitcode::CONFIG);
        }
        Err(e) => {
            eprintln!("Error: {e}");
            std::process::exit(exitcode::DATAERR);
        }
    }
}

fn run(cli: &Cli) -> Result<(), CliError> {
    let writer = get_writer(&cli.file)?;

    gen_random_lines(writer, cli.lines)?;

    println!(
        r#"Done, wrote {} line{} to "{}"."#,
        cli.lines.separate_with_commas(),
        if cli.lines == 1 { "" } else { "s" },
        cli.file.display()
    );

    Ok(())
}

fn get_writer(path: &Path) -> Result<impl Write, CliError> {
    let file = File::create(path).map_err(|e| CliError::FileCreate(path.to_owned(), e))?;
    Ok(BufWriter::new(file))
}
