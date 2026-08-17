use std::{
    fs,
    io::{self, BufWriter, Write},
    path::Path,
};

struct PrefixWriter<W> {
    inner: W,
    prefix: String,
}

impl<W: Write> Write for PrefixWriter<W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.inner.write_all(self.prefix.as_bytes())?;
        self.inner.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

pub fn get_writer(path: &Path, multiple_files: bool) -> Result<Box<dyn Write>, std::io::Error> {
    let stdout = std::io::stdout();

    if multiple_files {
        let full_path = fs::canonicalize(path)?;
        let prefix = format!("{}:", full_path.display());
        Ok(Box::new(PrefixWriter {
            inner: stdout,
            prefix,
        }))
    } else {
        Ok(Box::new(BufWriter::new(stdout.lock())))
    }
}
