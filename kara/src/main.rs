use std::env;

use crate::editor::Editor;

mod editor;

fn main() {
    let args: Vec<String> = env::args().collect();
    let file_name = args.get(1);

    let editor = Editor::builder().file(file_name).build();

    #[allow(clippy::unwrap_used)]
    editor.unwrap().run();
}
