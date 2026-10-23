use std::ops::Range;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Number,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Annotation {
    pub bytes: Range<usize>,
    pub kind: Kind,
}

#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct LexState;

pub fn line(text: &str, _incoming: LexState) -> (Vec<Annotation>, LexState) {
    let annotations = text
        .char_indices()
        .filter_map(|(byte, character)| {
            character.is_ascii_digit().then_some(Annotation {
                bytes: byte..byte.saturating_add(character.len_utf8()),
                kind: Kind::Number,
            })
        })
        .collect();
    (annotations, LexState)
}
