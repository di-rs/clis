use biggie::{GenerationOptions, LineEnding, generate};
use tailr::{TakeValue, print_stream_bytes};

fn main() -> std::io::Result<()> {
    for _ in 0..2 {
        let mut output = Vec::new();
        generate(
            &mut output,
            &GenerationOptions {
                lines: 2,
                words_per_line: 0..=0,
                seed: Some(17),
                line_ending: LineEnding::CrLf,
                final_newline: false,
                ..GenerationOptions::default()
            },
        )?;
        assert_eq!(output, b"\r\n");
        let mut tail = Vec::new();
        print_stream_bytes(b"a\0\xff".as_slice(), &mut tail, TakeValue::TakeNum(-2))?;
        assert_eq!(tail, b"\0\xff");
    }
    Ok(())
}
