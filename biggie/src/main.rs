use biggie::gen_random_lines;
use clap::Parser;
use color_eyre::eyre::{Context, Result};
use std::{
    fs::File,
    io::{BufWriter, Write},
    path::Path,
};
use thousands::Separable;

mod cli;
use crate::cli::Cli;

fn main() -> Result<()> {
    let cli = Cli::parse();
    error::init(&cli.verbosity)?;
    logging::init(&cli.verbosity);
    run(&cli)
}

fn run(cli: &Cli) -> Result<()> {
    let writer = get_writer(&cli.file)?;
    gen_random_lines(writer, cli.lines)?;

    println!(
        r#"Done, wrote {} line{} to "{}"."#,
        cli.lines.separate_with_commas(),
        if cli.lines == 1 { "" } else { "s" },
        cli.file.display()
    );
    log::info!("Wrote {} to {}", cli.lines.separate_with_commas(), cli.file.display());

    Ok(())
}

fn get_writer(path: &Path) -> Result<impl Write> {
    let file =
        File::create(path).wrap_err_with(|| format!("Cannot create file {}", path.display()))?;
    Ok(BufWriter::new(file))
}
