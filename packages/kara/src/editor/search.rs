use super::{Document, TextPosition};
use unicode_segmentation::UnicodeSegmentation;

#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub enum SearchDirection {
    #[default]
    Forward,
    Backward,
}

impl SearchDirection {
    pub const fn reversed(self) -> Self {
        match self {
            Self::Forward => Self::Backward,
            Self::Backward => Self::Forward,
        }
    }
}

#[derive(Default, Clone)]
pub struct Search {
    pub query: String,
    pub direction: SearchDirection,
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
        self.select(origin, self.direction, true);
    }

    pub fn select(&mut self, origin: TextPosition, direction: SearchDirection, inclusive: bool) {
        if self.matches.is_empty() {
            self.active = None;
            return;
        }
        let origin = (origin.line, origin.grapheme);
        self.active = Some(match direction {
            SearchDirection::Forward => self
                .matches
                .iter()
                .position(|at| {
                    let at = (at.line, at.grapheme);
                    at > origin || (inclusive && at == origin)
                })
                .unwrap_or(0),
            SearchDirection::Backward => self
                .matches
                .iter()
                .rposition(|at| {
                    let at = (at.line, at.grapheme);
                    at < origin || (inclusive && at == origin)
                })
                .unwrap_or_else(|| self.matches.len().saturating_sub(1)),
        });
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
        let boundaries: Vec<_> = line
            .grapheme_indices(true)
            .map(|(byte, _)| byte)
            .chain(std::iter::once(line.len()))
            .collect();
        let mut offset = 0;
        while let Some(relative) = line.get(offset..).and_then(|suffix| suffix.find(query)) {
            let byte = offset.saturating_add(relative);
            let end = byte.saturating_add(query.len());
            if let (Ok(grapheme), Ok(_)) = (
                boundaries.binary_search(&byte),
                boundaries.binary_search(&end),
            ) {
                matches.push(TextPosition {
                    line: line_index,
                    grapheme,
                });
                offset = end;
            } else {
                // Rejected candidates must not consume a later valid overlap.
                let next = boundaries.partition_point(|boundary| *boundary <= byte);
                let Some(next_byte) = boundaries.get(next) else {
                    break;
                };
                offset = *next_byte;
            }
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
    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions establish complete grapheme matching independently."
    )]
    fn rejects_partial_combining_and_emoji_sequences() -> Result<(), Box<dyn std::error::Error>> {
        let dir = assert_fs::TempDir::new()?;
        let path = dir.path().join("unicode");
        std::fs::write(
            &path,
            "e\u{301} e 👩‍💻 👩
",
        )?;
        let document = Document::open(path.to_str().ok_or("invalid path")?)?;
        assert_eq!(
            find(&document, "e"),
            vec![TextPosition {
                line: 0,
                grapheme: 2
            }]
        );
        assert_eq!(find(&document, "\u{301}"), []);
        assert_eq!(find(&document, "💻"), []);
        assert_eq!(
            find(&document, "👩"),
            vec![TextPosition {
                line: 0,
                grapheme: 6
            }]
        );
        assert_eq!(
            find(&document, "e\u{301}"),
            vec![TextPosition {
                line: 0,
                grapheme: 0
            }]
        );
        assert_eq!(
            find(&document, "👩‍💻"),
            vec![TextPosition {
                line: 0,
                grapheme: 4
            }]
        );
        Ok(())
    }
}
