use anyhow::{Context, Result};
use std::{
    fs::{self, File},
    io::{self, BufWriter, Write},
    path::Path,
};
use thousands::Separable;

/// Keep buffering concrete so every small domain write can inline the buffer path.
/// Destination dispatch occurs only when the buffer reaches the OS stream.
pub enum Destination {
    File(File),
    Stdout(io::StdoutLock<'static>),
}
impl Write for Destination {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        match self {
            Self::File(file) => file.write(bytes),
            Self::Stdout(stdout) => stdout.write(bytes),
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        match self {
            Self::File(file) => file.flush(),
            Self::Stdout(stdout) => stdout.flush(),
        }
    }
}

pub fn single(
    path: &Path,
    count: u64,
    unit: &str,
    generate: impl FnOnce(&mut BufWriter<Destination>) -> io::Result<()>,
) -> Result<()> {
    if path.as_os_str() == "-" {
        return buffered(
            Destination::Stdout(io::stdout().lock()),
            generate,
            count,
            unit,
        );
    }
    let file =
        File::create(path).with_context(|| format!("Cannot create file {}", path.display()))?;
    buffered(Destination::File(file), generate, count, unit)?;
    let mut stdout = io::stdout().lock();
    writeln!(
        stdout,
        "Done, wrote {} {}{} to \"{}\".",
        count.separate_with_commas(),
        unit,
        if count == 1 { "" } else { "s" },
        path.display()
    )
    .context("Cannot write completion message")?;
    stdout.flush().context("Cannot flush completion message")
}
fn buffered(
    writer: Destination,
    generate: impl FnOnce(&mut BufWriter<Destination>) -> io::Result<()>,
    count: u64,
    unit: &str,
) -> Result<()> {
    let mut writer = BufWriter::with_capacity(64 * 1024, writer);
    generate(&mut writer).context("Cannot write generated output")?;
    flush(&mut writer)?;
    log::info!("generated output: {count} {unit}s");
    Ok(())
}
pub fn flush(writer: &mut impl Write) -> Result<()> {
    let _span = tracing::debug_span!("flush").entered();
    writer.flush().context("Cannot flush generated output")
}
pub fn reject_alias(left: &Path, right: &Path) -> Result<()> {
    if left == right {
        anyhow::bail!("input and output paths must be distinct");
    }
    match (fs::metadata(left), fs::metadata(right)) {
        (Ok(a), Ok(b)) => {
            use std::os::unix::fs::MetadataExt;
            if a.dev() == b.dev() && a.ino() == b.ino() {
                anyhow::bail!("input and output paths refer to the same file");
            }
        }
        (Err(e), _) | (_, Err(e)) if e.kind() != io::ErrorKind::NotFound => return Err(e.into()),
        _ => {}
    }
    Ok(())
}

pub fn pair(left_path: &Path, right_path: &Path, options: &biggie::PairOptions) -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    options.validate()?;
    reject_alias(left_path, right_path)?;
    // Open without truncating, then compare actual identities as well as path metadata.
    // This catches aliases of newly created files and narrows path-change races.
    let open = |path: &Path| {
        fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .with_context(|| format!("Cannot create file {}", path.display()))
    };
    let left = open(left_path)?;
    let right = open(right_path)?;
    let a = left.metadata()?;
    let b = right.metadata()?;
    if a.dev() == b.dev() && a.ino() == b.ino() {
        anyhow::bail!("pair destinations refer to the same file");
    }
    left.set_len(0)
        .context("Cannot truncate left destination")?;
    right
        .set_len(0)
        .context("Cannot truncate right destination")?;
    let mut left = BufWriter::new(left);
    let mut right = BufWriter::new(right);
    biggie::generate_pair(&mut left, &mut right, options).context("Cannot write generated pair")?;
    let left_flush = flush(&mut left);
    let right_flush = flush(&mut right);
    left_flush?;
    right_flush?;
    let (left_count, right_count) = options.record_counts()?;
    log::info!("generated pair: {left_count} left records, {right_count} right records");
    let mut stdout = io::stdout().lock();
    writeln!(
        stdout,
        "Done, wrote {left_count} left and {right_count} right records."
    )
    .context("Cannot write completion message")?;
    stdout.flush().context("Cannot flush completion message")
}
