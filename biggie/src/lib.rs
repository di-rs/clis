use rand::RngExt;
use rand::distr::Alphanumeric;
use std::io::Write;

/// # Errors
/// Throws error when couldn't write to writer
pub fn gen_random_lines(mut writer: impl Write, num_lines: u64) -> Result<(), std::io::Error> {
    for _ in 0..num_lines {
        let num_words = rand::random_range(7..15);
        let mut words = vec![];
        for _ in 0..num_words {
            words.push(random_string());
        }
        writeln!(writer, "{}", words.join(" "))?;
    }
    Ok(())
}

fn random_string() -> String {
    let length = rand::random_range(2..12);
    rand::rng()
        .sample_iter(&Alphanumeric)
        .take(length)
        .map(char::from)
        .collect()
}
