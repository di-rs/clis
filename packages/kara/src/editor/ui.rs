use std::range::Range;

use ratatui::{
    Frame,
    buffer::Buffer,
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Widget},
};

use super::{
    Document, ScreenPosition, Size, document_status::DocumentStatus, state::EditorState,
    terminal::display_text, view::welcome_message::WelcomeMessage,
};

pub(super) fn render(frame: &mut Frame<'_>, state: &mut EditorState) {
    let area = frame.area();
    state.resize(Size {
        width: usize::from(area.width),
        height: usize::from(area.height),
    });
    if area.is_empty() {
        return;
    }
    state.search.refresh(&state.buffer);
    state.buffer.refresh_highlights();
    let caret = state.buffer.caret_location();
    state.view.scroll_into_view(caret);
    let document_area = Rect::new(area.x, area.y, area.width, area.height.saturating_sub(2));
    frame.render_widget(
        DocumentWidget {
            document: &state.buffer,
            search: &state.search,
            offset: state.view.scroll_offset,
        },
        document_area,
    );
    let bottom = area.bottom().saturating_sub(1);
    let message = state
        .prompt
        .as_ref()
        .map_or_else(|| state.message_bar.text(), super::prompt::Prompt::text);
    frame.render_widget(
        Paragraph::new(display_text(&message)),
        Rect::new(area.x, bottom, area.width, 1),
    );
    if area.height > 2 {
        let status = DocumentStatus::from(&state.buffer);
        let left = format!(
            "{} {}",
            display_text(&status.filename),
            status.modified_indicator_to_string()
        );
        let right = format!(
            "{} | {} | {}",
            state.mode,
            status.file_type,
            status.position_indicator_to_string()
        );
        let right_width = u16::try_from(Line::from(right.as_str()).width())
            .unwrap_or(u16::MAX)
            .min(area.width);
        let row = Rect::new(area.x, bottom.saturating_sub(1), area.width, 1);
        frame.render_widget(
            Paragraph::new(left).style(Style::new().add_modifier(Modifier::REVERSED)),
            Rect::new(row.x, row.y, row.width.saturating_sub(right_width), 1),
        );
        frame.render_widget(
            Paragraph::new(right).style(Style::new().add_modifier(Modifier::REVERSED)),
            Rect::new(
                row.right().saturating_sub(right_width),
                row.y,
                right_width,
                1,
            ),
        );
    }
    let cursor = state.prompt.as_ref().map_or_else(
        || ScreenPosition {
            x: caret.x.saturating_sub(state.view.scroll_offset.x),
            y: caret.y.saturating_sub(state.view.scroll_offset.y),
        },
        |prompt| ScreenPosition {
            x: prompt.caret_position_col(),
            y: usize::from(bottom.saturating_sub(area.y)),
        },
    );
    frame.set_cursor_position((
        area.x.saturating_add(
            u16::try_from(cursor.x)
                .unwrap_or(u16::MAX)
                .min(area.width.saturating_sub(1)),
        ),
        area.y.saturating_add(
            u16::try_from(cursor.y)
                .unwrap_or(u16::MAX)
                .min(area.height.saturating_sub(1)),
        ),
    ));
}

struct DocumentWidget<'a> {
    document: &'a Document,
    search: &'a super::search::Search,
    offset: ScreenPosition,
}

impl Widget for DocumentWidget<'_> {
    fn render(self, area: Rect, buffer: &mut Buffer) {
        for y in 0..area.height {
            let line_index = self.offset.y.saturating_add(usize::from(y));
            let line = self.document.get_line(line_index);
            let text = line.map_or_else(
                || Line::from("~"),
                |line| {
                    let spans = line
                        .visible(Range::from(
                            self.offset.x..self.offset.x.saturating_add(usize::from(area.width)),
                        ))
                        .into_iter()
                        .map(|(grapheme, text)| {
                            let syntax = line
                                .byte_range(grapheme)
                                .and_then(|bytes| self.document.syntax_at(line_index, bytes));
                            let search = self.search.match_at(super::TextPosition {
                                line: line_index,
                                grapheme,
                            });
                            let style = super::highlight::style(syntax, search);
                            Span::styled(text, style)
                        })
                        .collect::<Vec<_>>();
                    Line::from(spans)
                },
            );
            Paragraph::new(text).render(
                Rect::new(area.x, area.y.saturating_add(y), area.width, 1),
                buffer,
            );
        }
        if self.document.is_empty() && !area.is_empty() {
            let size = Size {
                width: usize::from(area.width),
                height: usize::from(area.height),
            };
            if let Some(message) = WelcomeMessage::draw(&size) {
                Paragraph::new(message).render(
                    Rect::new(
                        area.x,
                        area.y.saturating_add(area.height / 3),
                        area.width,
                        1,
                    ),
                    buffer,
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::{
        Terminal,
        backend::{Backend, TestBackend},
        style::{Color, Modifier},
    };

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions check rendered cells and cursor placement."
    )]
    fn draws_unicode_controls_bars_and_clipped_cells() -> Result<(), Box<dyn std::error::Error>> {
        let mut state = EditorState::default();
        for ch in "a界e\u{301}\u{1b}".chars() {
            state.buffer.insert_char(ch);
        }
        state
            .buffer
            .move_caret(super::super::document::Direction::StartOfBuffer);
        state
            .buffer
            .move_caret(super::super::document::Direction::StartOfLine);
        let mut terminal = Terminal::new(TestBackend::new(30, 6))?;
        terminal.draw(|frame| render(frame, &mut state))?;
        let cells = terminal.backend().buffer();
        assert_eq!(cells[(0, 0)].symbol(), "a");
        assert_eq!(cells[(1, 0)].symbol(), "界");
        assert_eq!(cells[(3, 0)].symbol(), "e\u{301}");
        assert_eq!(cells[(4, 0)].symbol(), "▯");
        assert!(cells[(0, 4)].modifier.contains(Modifier::REVERSED));
        assert_eq!(terminal.backend_mut().get_cursor_position()?, (0, 0).into());
        let mut clipped = Buffer::empty(Rect::new(0, 0, 2, 1));
        DocumentWidget {
            document: &state.buffer,
            search: &state.search,
            offset: ScreenPosition { x: 2, y: 0 },
        }
        .render(clipped.area, &mut clipped);
        assert_eq!(clipped[(0, 0)].symbol(), "⋯");
        assert_eq!(clipped[(1, 0)].symbol(), "e\u{301}");
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify tiny and resized terminal bounds."
    )]
    fn resize_keeps_cursor_inside_tiny_frames() -> Result<(), Box<dyn std::error::Error>> {
        let mut state = EditorState::default();
        for ch in "a long line".chars() {
            state.buffer.insert_char(ch);
        }
        let mut terminal = Terminal::new(TestBackend::new(1, 1))?;
        for (width, height) in [(1, 1), (2, 2), (12, 3), (80, 24), (0, 0)] {
            terminal.backend_mut().resize(width, height);
            terminal.resize(Rect::new(0, 0, width, height))?;
            terminal.draw(|frame| render(frame, &mut state))?;
            if width > 0 && height > 0 {
                let cursor = terminal.backend_mut().get_cursor_position()?;
                assert!(cursor.x < width && cursor.y < height);
            }
        }
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions inspect cells emitted to the terminal backend."
    )]
    fn wide_filenames_do_not_hide_status_text() -> Result<(), Box<dyn std::error::Error>> {
        let directory = assert_fs::TempDir::new()?;
        let path = directory.path().join("界".repeat(10));
        std::fs::write(&path, b"x")?;
        let mut state = EditorState::default();
        state.open_file(path.to_str().ok_or("invalid fixture path")?);
        assert_eq!(state.buffer.filename(), Some(path.as_path()));
        let prefix_width = Line::from(format!("{}/", directory.path().display())).width();
        let width = u16::try_from(prefix_width.saturating_add(30))?;
        let mut terminal = Terminal::new(TestBackend::new(width, 4))?;
        for width in [width, width.saturating_add(1)] {
            terminal.backend_mut().resize(width, 4);
            terminal.resize(Rect::new(0, 0, width, 4))?;
            terminal.draw(|frame| render(frame, &mut state))?;
            let cells = terminal.backend().buffer();
            let status: String = (0..width).map(|x| cells[(x, 2)].symbol()).collect();
            assert!(
                status.contains("NAVIGATE"),
                "status lost a mode cell: {status}"
            );
        }
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify search style composition, clipping, and invalidation."
    )]
    fn accepted_search_styles_survive_clipping_and_follow_edits()
    -> Result<(), Box<dyn std::error::Error>> {
        use crate::editor::{TextPosition, command::Edit, mode::EditorMode};
        let mut state = EditorState::default();
        for character in "界a界".chars() {
            state.buffer.insert_char(character);
        }
        state.buffer.set_position(TextPosition::default());
        state.change_mode(EditorMode::Search);
        state.handle_edit_command(&Edit::Insert('界'));
        state.handle_edit_command(&Edit::InsertNewline);
        let mut terminal = Terminal::new(TestBackend::new(12, 6))?;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].bg, Color::Yellow);
        assert!(
            terminal.backend().buffer()[(0, 0)]
                .modifier
                .contains(Modifier::BOLD)
        );
        assert_eq!(terminal.backend().buffer()[(3, 0)].bg, Color::Cyan);
        let mut clipped = Buffer::empty(Rect::new(0, 0, 2, 1));
        DocumentWidget {
            document: &state.buffer,
            search: &state.search,
            offset: ScreenPosition { x: 1, y: 0 },
        }
        .render(clipped.area, &mut clipped);
        assert_eq!(clipped[(0, 0)].symbol(), "⋯");
        assert_eq!(clipped[(0, 0)].bg, Color::Yellow);
        assert_eq!(clipped[(1, 0)].bg, Color::Reset);
        state.buffer.delete();
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].bg, Color::Reset);
        assert_eq!(terminal.backend().buffer()[(1, 0)].bg, Color::Cyan);
        state.change_mode(EditorMode::Search);
        state.handle_edit_command(&Edit::Insert('a'));
        state.change_mode(EditorMode::View);
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(state.search.query, "界");
        assert_eq!(terminal.backend().buffer()[(0, 0)].bg, Color::Reset);
        assert_eq!(terminal.backend().buffer()[(1, 0)].bg, Color::Cyan);
        state.handle_command(crate::editor::command::EditorCommand::ClearSearch);
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(1, 0)].bg, Color::Reset);
        assert_eq!(state.search.query, "界");
        state
            .handle_command(crate::editor::command::EditorCommand::RepeatSearch { reverse: false });
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(1, 0)].bg, Color::Yellow);
        Ok(())
    }
    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify full-grapheme syntax coloring and search precedence."
    )]
    fn non_numeric_graphemes_keep_search_styles() -> Result<(), Box<dyn std::error::Error>> {
        let fixture = assert_fs::TempDir::new()?;
        let path = fixture.path().join("grapheme.rs");
        std::fs::write(&path, "\u{600}1")?;
        let mut state = EditorState::default();
        state.buffer = Document::open(path.to_str().ok_or("non-UTF-8 fixture path")?)?;
        let mut terminal = Terminal::new(TestBackend::new(12, 6))?;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].symbol(), "\u{600}1");
        assert_eq!(terminal.backend().buffer()[(0, 0)].fg, Color::Reset);
        state.search.update(
            &state.buffer,
            "\u{600}1".to_owned(),
            super::super::TextPosition::default(),
        );
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].fg, Color::Black);
        assert_eq!(terminal.backend().buffer()[(0, 0)].bg, Color::Yellow);
        state.handle_command(crate::editor::command::EditorCommand::ClearSearch);
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].fg, Color::Reset);
        assert_eq!(terminal.backend().buffer()[(0, 0)].bg, Color::Reset);
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify digit colors and search precedence."
    )]
    fn colors_digits_below_search_styles() -> Result<(), Box<dyn std::error::Error>> {
        let mut state = EditorState::default();
        for character in "a 1 界 2".chars() {
            state.buffer.insert_char(character);
        }
        state
            .buffer
            .set_position(super::super::TextPosition::default());
        let mut terminal = Terminal::new(TestBackend::new(12, 6))?;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(0, 0)].fg, Color::Reset);
        assert_eq!(terminal.backend().buffer()[(2, 0)].fg, Color::Reset);
        let dir = assert_fs::TempDir::new()?;
        state.buffer.save_as(
            dir.path()
                .join("colors.rs")
                .to_str()
                .ok_or("invalid path")?,
        )?;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(2, 0)].fg, Color::Red);
        assert_eq!(terminal.backend().buffer()[(7, 0)].fg, Color::Red);
        state.search.update(
            &state.buffer,
            "1".to_owned(),
            super::super::TextPosition::default(),
        );
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(2, 0)].fg, Color::Black);
        assert_eq!(terminal.backend().buffer()[(2, 0)].bg, Color::Yellow);
        state.buffer.save_as(
            dir.path()
                .join("colors.txt")
                .to_str()
                .ok_or("invalid path")?,
        )?;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(2, 0)].bg, Color::Yellow);
        assert_eq!(terminal.backend().buffer()[(7, 0)].fg, Color::Reset);
        state.search.visible = false;
        terminal.draw(|frame| render(frame, &mut state))?;
        assert_eq!(terminal.backend().buffer()[(2, 0)].fg, Color::Reset);
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify source styling survives horizontal clipping."
    )]
    fn clipped_non_numeric_graphemes_keep_source_color() -> Result<(), Box<dyn std::error::Error>> {
        let directory = assert_fs::TempDir::new()?;
        let path = directory.path().join("keycap.rs");
        std::fs::write(&path, "1\u{fe0f}\u{20e3}")?;
        let mut document = Document::open(path.to_str().ok_or("invalid fixture path")?)?;
        document.refresh_highlights();
        for x in [0, 1] {
            let mut cells = Buffer::empty(Rect::new(0, 0, 1, 1));
            DocumentWidget {
                document: &document,
                search: &super::super::search::Search::default(),
                offset: ScreenPosition { x, y: 0 },
            }
            .render(cells.area, &mut cells);
            assert_eq!(cells[(0, 0)].symbol(), "⋯");
            assert_eq!(cells[(0, 0)].fg, Color::Reset);
        }
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify visible Rust category colors."
    )]
    fn colors_rust_token_categories() -> Result<(), Box<dyn std::error::Error>> {
        let dir = assert_fs::TempDir::new()?;
        let path = dir.path().join("colors.rs");
        std::fs::write(&path, b"let n: i32 = Some(12);\n'a'")?;
        let mut state = EditorState::default();
        state.open_file(path.to_str().ok_or("invalid path")?);
        let mut terminal = Terminal::new(TestBackend::new(40, 6))?;
        terminal.draw(|frame| render(frame, &mut state))?;
        for (column, color) in [
            (0, Color::Blue),
            (7, Color::Green),
            (13, Color::Magenta),
            (18, Color::Red),
        ] {
            assert_eq!(terminal.backend().buffer()[(column, 0)].fg, color);
        }
        assert_eq!(
            terminal.backend().buffer()[(0, 1)].fg,
            Color::Rgb(255, 191, 0)
        );
        Ok(())
    }
}
