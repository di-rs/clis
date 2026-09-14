use std::{fs::File, path::Path};

use crate::editor::{
    editorcommand::{
        EditorCommand,
        System::{Quit, QuitForce, SaveAs},
    },
    line::Line,
};

pub struct Interpreter {}

impl Interpreter {
    pub(crate) fn into_command(
        value: &Line,
        current_file: Option<&Path>,
    ) -> Result<EditorCommand, String> {
        let str = value.to_string();
        let mut split = str.split(' ');
        match split.next() {
            Some("w") => {
                let name = split
                    .next()
                    .or_else(|| current_file.and_then(|x| x.to_str()));
                name.map_or_else(
                    || Err("ERR: filename was not provided".to_string()),
                    // TODO: File creation should not be here
                    |name| match File::create(name) {
                        Err(e) => Err(format!("ERR: cannot open file {e}")),
                        Ok(_) => Ok(EditorCommand::System(SaveAs(name.to_owned()))),
                    },
                )
            }
            Some("q") => Ok(EditorCommand::System(Quit)),
            Some("q!") => Ok(EditorCommand::System(QuitForce)),
            _ => Err(format!("ERR: unknown command {str}")),
        }
    }
}
