use biggie::{ByteOptions, ByteSource};
use clap::Args;
use std::path::PathBuf;
#[derive(Clone, Debug)]
pub struct HexPattern(Vec<u8>);

#[derive(Args, Debug)]
pub struct ByteArgs {
    /// Output filename, or - for stdout
    #[arg(default_value = "out.txt")]
    pub file: PathBuf,
    /// Exact decimal byte budget, including zero
    #[arg(short = 'b', long)]
    pub bytes: u64,
    /// Repeating bytes as nonempty even-length hex
    #[arg(short='p',long,value_parser=parse_hex,conflicts_with="seed")]
    pub pattern_hex: Option<HexPattern>,
    /// Seed for random bytes
    #[arg(short = 's', long)]
    pub seed: Option<u64>,
}
impl ByteArgs {
    pub fn options(&self) -> ByteOptions {
        ByteOptions {
            bytes: self.bytes,
            source: self
                .pattern_hex
                .as_ref()
                .map_or(ByteSource::Random { seed: self.seed }, |p| {
                    ByteSource::Pattern(p.0.clone())
                }),
        }
    }
}
fn parse_hex(value: &str) -> Result<HexPattern, String> {
    if value.is_empty() || !value.len().is_multiple_of(2) || value.len() > 16 * 1024 * 1024 {
        return Err("expected 1 byte through 8 MiB of even-length hex".into());
    }
    value
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| match pair {
            [a, b] => u8::try_from(
                char::from(*a)
                    .to_digit(16)
                    .ok_or("invalid hex digit")?
                    .saturating_mul(16)
                    .saturating_add(char::from(*b).to_digit(16).ok_or("invalid hex digit")?),
            )
            .map_err(|e| e.to_string()),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(HexPattern)
}
