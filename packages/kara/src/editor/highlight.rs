mod lexer;
use super::{buffer::Buffer, search::MatchKind};
pub use lexer::Kind;
use lexer::{Annotation, LexState};
use ratatui::style::{Color, Modifier, Style};

#[derive(Default)]
struct CachedLine {
    annotations: Vec<Annotation>,
    outgoing: LexState,
    valid: bool,
}

#[derive(Default)]
pub struct HighlightCache {
    lines: Vec<CachedLine>,
    dirty_from: Option<usize>,
    dirty_through: usize,
    #[cfg(test)]
    recomputed: usize,
}

impl HighlightCache {
    /// Align cached lines with a single edit, including splitting or joining lines.
    pub fn invalidate(&mut self, line: usize, old_height: usize, new_height: usize) {
        if self.lines.len() != old_height {
            self.lines.clear();
            self.dirty_from = Some(0);
            self.dirty_through = new_height.saturating_sub(1);
            return;
        }
        let added = new_height.saturating_sub(old_height);
        let removed = old_height.saturating_sub(new_height);
        let at = line.min(self.lines.len());
        if added > 0 {
            self.lines.splice(
                at..at,
                std::iter::repeat_with(CachedLine::default).take(added),
            );
            if self.dirty_through >= line {
                self.dirty_through = self.dirty_through.saturating_add(added);
            }
        } else if removed > 0 {
            self.lines
                .drain(at..at.saturating_add(removed).min(self.lines.len()));
            if self.dirty_through >= line {
                self.dirty_through = self.dirty_through.saturating_sub(removed);
            }
        }
        self.dirty_from = Some(self.dirty_from.map_or(line, |first| first.min(line)));
        self.dirty_through = self.dirty_through.max(line.saturating_add(added));
    }

    pub fn refresh(&mut self, text: &Buffer) {
        if self.lines.len() != text.height() {
            let start = self.lines.len().min(text.height());
            self.lines.resize_with(text.height(), CachedLine::default);
            self.dirty_from = Some(self.dirty_from.map_or(start, |first| first.min(start)));
            self.dirty_through = text.height().saturating_sub(1);
        }
        let Some(start) = self.dirty_from.take() else {
            return;
        };
        let mut incoming = start
            .checked_sub(1)
            .and_then(|index| self.lines.get(index))
            .map_or_else(LexState::default, |line| line.outgoing);
        for index in start..text.height() {
            let source = text.line_text(index).unwrap_or_default();
            let (annotations, outgoing) = lexer::line(&source, incoming);
            let Some(cached) = self.lines.get_mut(index) else {
                break;
            };
            let stable = cached.valid && cached.outgoing == outgoing;
            *cached = CachedLine {
                annotations,
                outgoing,
                valid: true,
            };
            #[cfg(test)]
            {
                self.recomputed = self.recomputed.saturating_add(1);
            }
            incoming = outgoing;
            if index >= self.dirty_through && stable {
                break;
            }
        }
        self.dirty_through = 0;
    }

    /// Use the first source annotation that overlaps the complete grapheme.
    pub fn kind_overlapping(&self, line: usize, bytes: std::ops::Range<usize>) -> Option<Kind> {
        if bytes.is_empty() {
            return None;
        }
        let annotations = &self.lines.get(line)?.annotations;
        let index = annotations.partition_point(|annotation| annotation.bytes.end <= bytes.start);
        let annotation = annotations.get(index)?;
        (annotation.bytes.start < bytes.end).then_some(annotation.kind)
    }

    #[cfg(test)]
    pub fn kind_at(&self, line: usize, byte: usize) -> Option<Kind> {
        self.kind_overlapping(line, byte..byte.saturating_add(1))
    }
}

pub fn style(syntax: Option<Kind>, search: Option<MatchKind>) -> Style {
    match search {
        Some(MatchKind::Active) => Style::new()
            .fg(Color::Black)
            .bg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
        Some(MatchKind::Other) => Style::new().fg(Color::Black).bg(Color::Cyan),
        None => match syntax {
            Some(Kind::Number) => Style::new().fg(Color::Red),
            None => Style::default(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::TextPosition;

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify annotation overlap at grapheme boundaries."
    )]
    fn finds_annotations_inside_complete_graphemes() -> Result<(), Box<dyn std::error::Error>> {
        let text = Buffer::read("\u{600}1x2".as_bytes())?;
        let mut cache = HighlightCache::default();
        cache.refresh(&text);
        assert_eq!(cache.kind_overlapping(0, 0..3), Some(Kind::Number));
        assert_eq!(cache.kind_overlapping(0, 0..2), None);
        assert_eq!(cache.kind_overlapping(0, 3..4), None);
        assert_eq!(cache.kind_overlapping(0, 4..5), Some(Kind::Number));
        assert_eq!(cache.kind_overlapping(0, 2..2), None);
        assert_eq!(cache.kind_at(0, 0), None);
        assert_eq!(cache.kind_at(0, 2), Some(Kind::Number));
        let Some(line) = text.get_line(0) else {
            return Err("missing fixture line".into());
        };
        assert_eq!(line.byte_range(0), Some(0..3));
        assert_eq!(line.byte_range(1), Some(3..4));
        assert_eq!(line.byte_range(3), None);
        Ok(())
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify incremental invalidation and line alignment."
    )]
    fn cache_stops_at_stable_state_and_tracks_split_lines() -> Result<(), Box<dyn std::error::Error>>
    {
        let mut text = Buffer::read(b"12\n34\n56".as_slice())?;
        let mut cache = HighlightCache::default();
        cache.refresh(&text);
        assert_eq!(cache.recomputed, 3);
        text.insert_char(
            '9',
            TextPosition {
                line: 1,
                grapheme: 1,
            },
        );
        cache.invalidate(1, 3, 3);
        cache.refresh(&text);
        assert_eq!(cache.recomputed, 4);
        assert_eq!(cache.kind_at(1, 2), Some(Kind::Number));
        cache.refresh(&text);
        assert_eq!(cache.recomputed, 4);
        text.insert_newline(TextPosition {
            line: 1,
            grapheme: 1,
        });
        cache.invalidate(1, 3, 4);
        cache.refresh(&text);
        assert_eq!(cache.recomputed, 6);
        assert_eq!(cache.kind_at(2, 1), Some(Kind::Number));
        assert_eq!(cache.kind_at(2, 2), None);
        assert_eq!(cache.kind_at(3, 1), Some(Kind::Number));
        assert!(text.delete(TextPosition {
            line: 1,
            grapheme: 1
        }));
        cache.invalidate(1, 4, 3);
        cache.refresh(&text);
        assert_eq!(cache.kind_at(1, 2), Some(Kind::Number));
        assert_eq!(cache.kind_at(2, 1), Some(Kind::Number));
        Ok(())
    }
}
