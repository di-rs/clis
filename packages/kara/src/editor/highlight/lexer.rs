use std::ops::Range;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Number,
    Keyword,
    Type,
    Literal,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Annotation {
    pub bytes: Range<usize>,
    pub kind: Kind,
}

#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct LexState;

pub fn line(text: &str, _incoming: LexState) -> (Vec<Annotation>, LexState) {
    let mut annotations = Vec::new();
    let mut byte = 0;
    while let Some(remaining) = text.get(byte..) {
        let Some(word) = next_word(remaining) else {
            break;
        };
        let length = number_length(remaining, word)
            .filter(|length| token_is_delimited(text, byte, byte.saturating_add(*length)));
        let end = byte.saturating_add(length.unwrap_or(word.len()));
        if length.is_some() {
            annotations.push(Annotation {
                bytes: byte..end,
                kind: Kind::Number,
            });
        } else if let Some(kind) = word_kind(word)
            && token_is_delimited(text, byte, byte.saturating_add(word.len()))
        {
            annotations.push(Annotation {
                bytes: byte..byte.saturating_add(word.len()),
                kind,
            });
        }
        byte = end;
    }
    (annotations, LexState)
}

// Rust punctuation separates tokens even where Unicode word boundaries do
// not (notably commas and semicolons between digits). Keep Unicode identifier
// characters together, and preserve internal decimal dots for validation.
fn next_word(text: &str) -> Option<&str> {
    let first = text.chars().next()?;
    let is_separator = |character: char| {
        character.is_whitespace() || (character.is_ascii_punctuation() && character != '_')
    };
    if is_separator(first) {
        return text.get(..first.len_utf8());
    }
    let end = text
        .char_indices()
        .find(|(byte, character)| {
            if *character == '.' && first.is_ascii_digit() {
                let after_dot = text
                    .get(byte.saturating_add(1)..)
                    .and_then(|suffix| suffix.chars().next());
                if after_dot.is_some_and(|next| next.is_ascii_digit() || next == '_') {
                    return false;
                }
            }
            is_separator(*character)
        })
        .map_or(text.len(), |(byte, _)| byte);
    text.get(..end)
}

fn token_is_delimited(text: &str, start: usize, end: usize) -> bool {
    // A Unicode word boundary can occur within an identifier, e.g. 界12.
    // Rust operators/separators are ASCII. Conservatively keep adjacent
    // non-whitespace Unicode characters (including combining marks) attached
    // instead of guessing their Unicode identifier category.
    let before = text
        .get(..start)
        .and_then(|prefix| prefix.chars().next_back());
    let after = text.get(end..).and_then(|suffix| suffix.chars().next());
    let is_separator = |character: char| {
        character.is_whitespace() || (character.is_ascii_punctuation() && character != '_')
    };
    before.is_none_or(is_separator) && after.is_none_or(is_separator)
}

fn word_kind(word: &str) -> Option<Kind> {
    match word {
        "as" | "async" | "await" | "break" | "const" | "continue" | "crate" | "dyn" | "else"
        | "enum" | "extern" | "fn" | "for" | "if" | "impl" | "in" | "let" | "loop" | "match"
        | "mod" | "move" | "mut" | "pub" | "ref" | "return" | "self" | "Self" | "static"
        | "struct" | "super" | "trait" | "type" | "unsafe" | "use" | "where" | "while"
        | "abstract" | "become" | "box" | "do" | "final" | "gen" | "macro" | "override"
        | "priv" | "typeof" | "unsized" | "virtual" | "yield" | "try" | "union" | "macro_rules"
        | "raw" | "safe" => Some(Kind::Keyword),
        "i8" | "i16" | "i32" | "i64" | "i128" | "isize" | "u8" | "u16" | "u32" | "u64" | "u128"
        | "usize" | "f32" | "f64" | "bool" | "char" | "Option" | "Result" | "String" | "str"
        | "Vec" | "HashMap" => Some(Kind::Type),
        "Some" | "None" | "Ok" | "Err" | "true" | "false" => Some(Kind::Literal),
        _ => None,
    }
}

fn number_length(remaining: &str, word: &str) -> Option<usize> {
    if is_radix(word) {
        return Some(word.len());
    }
    // Word boundaries separate a signed exponent; include its sign and digits.
    if word.ends_with(['e', 'E']) {
        let suffix = remaining.get(word.len()..)?;
        if suffix.starts_with(['+', '-']) {
            let digits = next_word(suffix.get(1..)?)?;
            let length = word.len().saturating_add(1).saturating_add(digits.len());
            if is_decimal(remaining.get(..length)?) {
                return Some(length);
            }
        }
    }
    is_decimal(word).then_some(word.len())
}

fn is_radix(word: &str) -> bool {
    let base = match word.get(..2) {
        Some("0b" | "0B") => 2,
        Some("0o" | "0O") => 8,
        Some("0x" | "0X") => 16,
        _ => return false,
    };
    let digits = word.get(2..).unwrap_or_default();
    !digits.ends_with('_')
        && digits.chars().any(|character| character.is_digit(base))
        && digits
            .chars()
            .all(|character| character == '_' || character.is_digit(base))
}

fn is_decimal(word: &str) -> bool {
    if word.starts_with('_') || word.ends_with('_') {
        return false;
    }
    let clean: String = word.chars().filter(|character| *character != '_').collect();
    let (mantissa, exponent) = clean
        .split_once(['e', 'E'])
        .map_or((clean.as_str(), None), |(base, exponent)| {
            (base, Some(exponent))
        });
    if let Some(exponent) = exponent {
        let digits = exponent.strip_prefix(['+', '-']).unwrap_or(exponent);
        if !is_integer(digits) {
            return false;
        }
    }
    mantissa.split_once('.').map_or_else(
        || is_integer(mantissa),
        |(whole, fraction)| is_integer(whole) && is_integer(fraction),
    )
}

fn is_integer(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tokens(text: &str) -> Vec<&str> {
        line(text, LexState)
            .0
            .into_iter()
            .filter(|annotation| annotation.kind == Kind::Number)
            .filter_map(|annotation| text.get(annotation.bytes))
            .collect()
    }

    #[test]
    fn decimal_tokens_cover_floats_exponents_and_separators() {
        assert_eq!(
            tokens("123 1.25 50e10 1_000 1.2e-3 4E+2 1e_2"),
            ["123", "1.25", "50e10", "1_000", "1.2e-3", "4E+2", "1e_2"]
        );
        assert_eq!(
            tokens("u32 abc10 10bdh _1_000 1_000_ 1.2.3 1ee2 é12 1e+"),
            Vec::<&str>::new()
        );
        assert_eq!(tokens("(12)+34/56;"), ["12", "34", "56"]);
    }

    #[test]
    fn numeric_punctuation_has_independent_annotation_ranges() {
        for (text, expected) in [
            ("f(1,2)", vec![2..3, 4..5]),
            ("[1,2,3]", vec![1..2, 3..4, 5..6]),
            ("[1;2]", vec![1..2, 3..4]),
            ("f(1e-2,3E+4)", vec![2..6, 7..11]),
            ("[1.2e-3;4]", vec![1..7, 8..9]),
            ("界 (12,34)", vec![5..7, 8..10]),
        ] {
            let ranges: Vec<_> = line(text, LexState)
                .0
                .into_iter()
                .map(|annotation| annotation.bytes)
                .collect();
            assert_eq!(ranges, expected, "{text}");
        }
    }

    #[test]
    fn unicode_identifier_boundaries_do_not_expose_numeric_fragments() {
        for text in [
            "界12",
            "12界",
            "界\u{301}12",
            "12界\u{301}",
            "界1e-2界",
            "界\u{301}1e-2界",
            "_界12",
            "e\u{301}12",
            "a\u{203f}12",
            "℘12",
            "℘\u{301}12",
        ] {
            assert_eq!(tokens(text), Vec::<&str>::new(), "{text}");
        }
        assert_eq!(tokens("let 界\u{301}12 = 3;"), ["3"]);
        assert_eq!(tokens("界,12;34"), ["12", "34"]);
    }
    #[test]
    fn radix_tokens_validate_digits_and_prefixes() {
        assert_eq!(
            tokens("0xFF 0XdeAd 0b1010 0B10 0o77 0O17 0xCA_FE"),
            [
                "0xFF", "0XdeAd", "0b1010", "0B10", "0o77", "0O17", "0xCA_FE"
            ]
        );
        assert_eq!(tokens("0x 0b2 0o8 0xGG abc0xFF 0x_FF_"), Vec::<&str>::new());
    }
    #[test]
    fn recognizes_complete_keywords_types_and_literals() {
        let text = "let Option Some true i32 mylet enum_value False";
        let actual: Vec<_> = line(text, LexState)
            .0
            .into_iter()
            .filter_map(|annotation| {
                text.get(annotation.bytes)
                    .map(|word| (word, annotation.kind))
            })
            .collect();
        assert_eq!(
            actual,
            [
                ("let", Kind::Keyword),
                ("Option", Kind::Type),
                ("Some", Kind::Literal),
                ("true", Kind::Literal),
                ("i32", Kind::Type)
            ]
        );
    }
    #[test]
    fn weak_keywords_and_unicode_identifiers_keep_complete_tokens() {
        for (text, expected) in [
            ("macro_rules! example { () => {} }", vec!["macro_rules"]),
            ("&raw const value", vec!["raw", "const"]),
            (
                "unsafe extern C { safe fn test(); }",
                vec!["unsafe", "extern", "safe", "fn"],
            ),
            ("界fn fn界 ℘Option e\u{301}true", vec![]),
        ] {
            let actual: Vec<_> = line(text, LexState)
                .0
                .into_iter()
                .filter_map(|annotation| text.get(annotation.bytes))
                .collect();
            assert_eq!(actual, expected, "{text}");
        }
    }
}
