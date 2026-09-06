#![feature(never_type)]

use std::env;

use crate::editor::Editor;

mod editor;

fn main() -> color_eyre::Result<()> {
    color_eyre::install()?;

    let args: Vec<String> = env::args().collect();
    let file_name = args.get(1);

    let mut editor = Editor::builder().file(file_name).build()?;
    editor.run();

    Ok(())
}
