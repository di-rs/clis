use std::io::{BufWriter, Write};

pub fn get_writer() -> impl Write {
    let stdout = std::io::stdout();

    BufWriter::new(stdout.lock())
}
