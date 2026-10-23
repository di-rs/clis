use super::{Document, TextPosition};
use unicode_segmentation::UnicodeSegmentation;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchDirection {
    Forward,
    Backward,
}

#[derive(Default, Clone)]
pub struct Search {
    pub query: String,
    pub matches: Vec<TextPosition>,
    pub active: Option<usize>,
    pub visible: bool,
    revision: u64,
    match_length: usize,
}

impl Search {
    pub fn update(&mut self, document: &Document, query: String, origin: TextPosition) {
        self.query = query;
        self.match_length = self.query.graphemes(true).count();
        self.visible = true;
        self.revision = document.revision();
        self.matches = find(document, &self.query);
        self.active = if self.matches.is_empty() {
            None
        } else {
            Some(
                self.matches
                    .iter()
                    .position(|at| (at.line, at.grapheme) >= (origin.line, origin.grapheme))
                    .unwrap_or(0),
            )
        };
    }

    pub fn refresh(&mut self, document: &Document) {
        if self.revision != document.revision() {
            self.matches = find(document, &self.query);
            self.active = self
                .matches
                .iter()
                .position(|position| *position == document.cursor().position);
            self.revision = document.revision();
        }
    }

    pub fn match_at(&self, position: TextPosition) -> Option<MatchKind> {
        if !self.visible {
            return None;
        }
        let index = self
            .matches
            .partition_point(|start| {
                (start.line, start.grapheme) <= (position.line, position.grapheme)
            })
            .checked_sub(1)?;
        let start = self.matches.get(index)?;
        if start.line != position.line
            || position.grapheme >= start.grapheme.saturating_add(self.match_length)
        {
            return None;
        }
        Some(if self.active == Some(index) {
            MatchKind::Active
        } else {
            MatchKind::Other
        })
    }

    pub fn step(&mut self, direction: SearchDirection) {
        self.active = self.active.map(|index| match direction {
            SearchDirection::Forward => {
                let next = index.saturating_add(1);
                if next < self.matches.len() { next } else { 0 }
            }
            SearchDirection::Backward => index
                .checked_sub(1)
                .unwrap_or_else(|| self.matches.len().saturating_sub(1)),
        });
    }

    pub fn current(&self) -> Option<TextPosition> {
        self.active
            .and_then(|index| self.matches.get(index))
            .copied()
    }
}

pub enum MatchKind {
    Active,
    Other,
}

pub struct SearchSnapshot {
    pub cursor: super::cursor::Cursor,
    pub previous: Search,
    pub scroll_offset: super::ScreenPosition,
}

/// Search one rope line at a time; byte offsets never escape as text coordinates.
pub fn find(document: &Document, query: &str) -> Vec<TextPosition> {
    if query.is_empty() || query.contains(['\r', '\n']) {
        return Vec::new();
    }
    let mut matches = Vec::new();
    for line_index in 0..document.height() {
        let Some(line) = document.line_text(line_index) else {
            continue;
        };
        let boundaries: Vec<_> = line.grapheme_indices(true).map(|(byte, _)| byte).collect();
        for (byte, _) in line.match_indices(query) {
            let grapheme = boundaries.partition_point(|boundary| *boundary < byte);
            matches.push(TextPosition {
                line: line_index,
                grapheme,
            });
        }
    }
    matches
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Independent coordinates verify Unicode and rope chunk traversal."
    )]
    fn searches_unicode_lines_and_non_overlapping_matches() -> Result<(), Box<dyn std::error::Error>>
    {
        let dir = assert_fs::TempDir::new()?;
        let path = dir.path().join("text");
        fs::write(&path, format!("é界 hit\r\n{}hit\naaaa\n", "x".repeat(4095)))?;
        let document = Document::open(path.to_str().ok_or("invalid path")?)?;
        assert_eq!(
            find(&document, "hit"),
            vec![
                TextPosition {
                    line: 0,
                    grapheme: 3
                },
                TextPosition {
                    line: 1,
                    grapheme: 4095
                }
            ]
        );
        assert_eq!(
            find(&document, "aa"),
            vec![
                TextPosition {
                    line: 2,
                    grapheme: 0
                },
                TextPosition {
                    line: 2,
                    grapheme: 2
                }
            ]
        );
        assert_eq!(find(&document, ""), []);
        assert_eq!(find(&document, "HIT"), []);
        assert_eq!(find(&document, "hit\r\nx"), []);
        Ok(())
    }
}
