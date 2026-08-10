use clap::Parser;

#[derive(Debug, Parser)]
#[command(author, version, about)]
/// Rust version of `echo`
struct Args {
    /// Input text
    #[arg(required(true))]
    text: Vec<String>,
    
    /// Do not print newline
    #[arg(short('n'))]
    omit_newline: bool,
}

fn main() {
    let Args { text, omit_newline } = Args::parse();

    let ending = if omit_newline { "" } else { "\n" };

    print!("{}{ending}", text.join(" "));
}
