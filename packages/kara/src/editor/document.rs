use super::{
    ScreenPosition, TextPosition, buffer::Buffer, cursor::Cursor, line_layout::LineLayout,
    persistence,
};
pub use super::{cursor::Direction, persistence::SaveOutcome};
use std::{
    path::{Path, PathBuf},
    time::Instant,
};

#[derive(Default)]
pub struct Document {
    text: Buffer,
    cursor: Cursor,
    filename: Option<PathBuf>,
    modified_at: Option<Instant>,
}
impl Document {
    pub fn open(filename: &str) -> std::io::Result<Self> {
        Ok(Self {
            text: persistence::open(Path::new(filename))?,
            filename: Some(filename.into()),
            ..Self::default()
        })
    }
    pub fn save(&mut self) -> std::io::Result<SaveOutcome> {
        let outcome = self
            .filename
            .as_ref()
            .map_or(Ok(SaveOutcome::Saved), |path| {
                persistence::save(&self.text, path)
            })?;
        self.modified_at = None;
        Ok(outcome)
    }
    pub fn save_as(&mut self, filename: &str) -> std::io::Result<SaveOutcome> {
        self.save_as_with(filename, persistence::save)
    }
    fn save_as_with(
        &mut self,
        filename: &str,
        save: impl FnOnce(&Buffer, &Path) -> std::io::Result<SaveOutcome>,
    ) -> std::io::Result<SaveOutcome> {
        let path = Path::new(filename);
        let outcome = save(&self.text, path)?;
        self.filename = Some(path.to_path_buf());
        self.modified_at = None;
        Ok(outcome)
    }
    pub fn filename(&self) -> Option<&Path> {
        self.filename.as_deref()
    }
    pub const fn has_file(&self) -> bool {
        self.filename.is_some()
    }
    pub fn insert_char(&mut self, character: char) {
        let old_len = self.current_line_width();
        self.text.insert_char(character, self.cursor.position);
        if self.current_line_width() > old_len {
            self.move_caret(Direction::Right(1));
        }
        self.mark_modified();
    }
    pub fn insert_newline(&mut self) {
        self.text.insert_newline(self.cursor.position);
        self.move_caret(Direction::Right(1));
        self.mark_modified();
    }
    pub fn delete_backward(&mut self) {
        if self.cursor.position == TextPosition::default() {
            return;
        }
        self.move_caret(Direction::Left(1));
        self.delete();
    }
    pub fn delete(&mut self) {
        if self.text.delete(self.cursor.position) {
            self.mark_modified();
            self.cursor.clamp_after_edit(&self.text);
        }
    }
    pub fn move_caret(&mut self, direction: Direction) {
        self.cursor.move_caret(&self.text, direction);
    }
    pub fn caret_location(&self) -> ScreenPosition {
        ScreenPosition {
            y: self.cursor.position.line,
            x: self
                .text
                .get_line(self.cursor.position.line)
                .map_or(0, |line| line.width_until(self.cursor.position.grapheme)),
        }
    }
    pub const fn is_empty(&self) -> bool {
        self.text.is_empty()
    }
    pub fn get_line(&self, index: usize) -> Option<LineLayout> {
        self.text.get_line(index)
    }
    fn current_line_width(&self) -> usize {
        self.text
            .get_line(self.cursor.position.line)
            .map_or(0, |line| line.len())
    }
    fn mark_modified(&mut self) {
        self.modified_at = Some(Instant::now());
    }
    pub const fn is_modified(&self) -> bool {
        self.modified_at.is_some()
    }
    pub const fn height(&self) -> usize {
        self.text.height()
    }
}

#[cfg(test)]
mod save_tests {
    use super::{Document, SaveOutcome, persistence};
    use std::{fs, io};

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify the save commit boundary."
    )]
    fn directory_sync_failure_keeps_committed_identity() -> Result<(), Box<dyn std::error::Error>> {
        let dir = assert_fs::TempDir::new()?;
        let original = dir.path().join("original");
        let destination = dir.path().join("saved");
        fs::write(&original, b"original\n")?;
        let mut buffer = Document::open(original.to_str().ok_or("non-UTF-8 fixture")?)?;
        buffer.insert_char('X');
        let result = buffer.save_as_with(
            destination.to_str().ok_or("non-UTF-8 fixture")?,
            |text, path| {
                persistence::save_with_sync(text, path, |_| {
                    Err(io::Error::other("injected directory sync failure"))
                })
            },
        )?;
        assert!(matches!(result, SaveOutcome::DurabilityWarning(_)));
        assert_eq!(buffer.filename(), Some(destination.as_path()));
        assert!(!buffer.is_modified());
        assert_eq!(fs::read(&destination)?, b"Xoriginal\n");
        buffer.insert_char('Y');
        assert!(matches!(buffer.save()?, SaveOutcome::Saved));
        assert_eq!(fs::read(&destination)?, b"XYoriginal\n");
        assert_eq!(fs::read(&original)?, b"original\n");
        Ok(())
    }
}
