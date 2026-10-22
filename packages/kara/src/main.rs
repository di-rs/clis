use crate::editor::Editor;
use clap::Parser;
use std::process::ExitCode;
mod editor;

#[derive(Parser)]
#[command(version, about = "A small modal terminal text editor")]
struct Cli {
    /// File to edit; omit for an unnamed buffer
    file: Option<String>,
}

fn main() -> ExitCode {
    let session = cli_log::init("kara");
    session.set_stderr_level(log::LevelFilter::Off);
    let cli = match session.parse::<Cli>() {
        Ok(cli) => cli,
        Err(status) => return status,
    };
    let result = run(&cli);
    if let Err(error) = &result {
        cli_log::diagnostic!("kara: {error:#}");
    }
    session.finish(i32::from(result.is_err()))
}

fn run(cli: &Cli) -> anyhow::Result<()> {
    let mut editor = Editor::builder().file(cli.file.as_ref()).build()?;
    editor.run()?;
    Ok(())
}
