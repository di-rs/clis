use line_prefixer::PrefixWriter;
use std::{
    io::{BufWriter, Write},
    path::Path,
};

pub fn get_writer(path: &Path, multiple_files: bool) -> Box<dyn Write + '_> {
    if multiple_files {
        let prefix_writer = get_file_prefix_writer(path);
        Box::new(prefix_writer)
    } else {
        Box::new(get_stdout_writer())
    }
}

fn get_stdout_writer() -> impl Write {
    let stdout = std::io::stdout();
    BufWriter::new(stdout.lock())
}

fn get_file_prefix_writer(path: &Path) -> impl Write {
    let prefix = format!("{}:", path.display());
    let writer = get_stdout_writer();

    PrefixWriter::new(writer, prefix)
}
