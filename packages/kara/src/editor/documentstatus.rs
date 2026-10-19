use super::EditorBuffer;

#[derive(Default, Eq, PartialEq, Debug)]
pub struct DocumentStatus {
    total_lines: usize,
    current_line_index: usize,
    is_modified: bool,
    pub(crate) filename: String,
}

impl DocumentStatus {
    pub fn modified_indicator_to_string(&self) -> String {
        if self.is_modified {
            String::from("(*)")
        } else {
            String::new()
        }
    }

    pub fn position_indicator_to_string(&self) -> String {
        format!(
            "{}:{}",
            self.current_line_index.saturating_add(1),
            self.total_lines
        )
    }
}

impl From<&EditorBuffer> for DocumentStatus {
    fn from(buffer: &EditorBuffer) -> Self {
        let total_lines = buffer.height();
        let current_line_index = buffer.caret_location().y;
        let is_modified = buffer.is_modified();
        let filename: String = buffer
            .filename()
            .map_or_else(|| "[No Name]".to_owned(), |p| format!("{}", p.display()));

        Self {
            total_lines,
            current_line_index,
            is_modified,
            filename,
        }
    }
}
