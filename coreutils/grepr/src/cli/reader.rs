use std::io::{BufReader, IsTerminal};
use std::path::Path;
use std::{
    fs::File,
    io::{BufRead, stdin},
};

use crate::cli::CliError;

pub fn get_reader(path: &Path) -> Result<Box<dyn BufRead>, CliError> {
    if path == Path::new("-") {
        if stdin().is_terminal() {
            Err(CliError::Config)
        } else {
            Ok(Box::new(BufReader::new(stdin().lock())))
        }
    } else {
        Ok(Box::new(BufReader::new(File::open(path)?)))
    }
}
