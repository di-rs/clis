use super::{Document, TextPosition};
use unicode_segmentation::UnicodeSegmentation;

#[derive(Default, Clone)]
pub struct Search {
    pub query: String,
    pub matches: Vec<TextPosition>,
    pub active: Option<usize>,
}

impl Search {
    pub fn update(&mut self, document: &Document, query: String, origin: TextPosition) {
        self.query = query;
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

    pub fn next(&mut self) {
        self.active = self.active.map(|index| {
            let next = index.saturating_add(1);
            if next < self.matches.len() { next } else { 0 }
        });
    }

    pub fn current(&self) -> Option<TextPosition> {
        self.active
            .and_then(|index| self.matches.get(index))
            .copied()
    }
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
