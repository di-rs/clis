use std::env;

use crate::editor::Editor;

mod editor;

fn main() -> color_eyre::Result<()> {
    color_eyre::install()?;

    let session = cli_log::init("kara");
    // Console logging would overwrite the editor's terminal display.
    session.set_stderr_level(log::LevelFilter::Off);
    let result = run();
    if let Err(error) = &result {
        cli_log::record_error(format_args!("{error:?}"));
    }
    let _ = session.finish(i32::from(result.is_err()));
    result
}

fn run() -> color_eyre::Result<()> {
    let args: Vec<String> = env::args().collect();
    let file_name = args.get(1);

    let mut editor = Editor::builder().file(file_name).build()?;
    editor.run();

    Ok(())
}
