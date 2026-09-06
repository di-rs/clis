use csv::StringRecord;
use std::{
    io::{BufRead, Write},
    range::Range,
};

mod positionlist;
use crate::positionlist::PositionList;
pub use positionlist::ParseError;

#[derive(Debug)]
pub enum Extract {
    Fields(PositionList),
    Bytes(PositionList),
    Chars(PositionList),
}

impl Extract {
    /// # Errors
    /// Throws error if unable to decode line in the reader or write to writer is failed
    pub fn extract(
        &self,
        reader: impl BufRead,
        mut writer: impl Write,
        delimiter: u8,
    ) -> Result<(), std::io::Error> {
        match self {
            Self::Bytes(byte_pos) => {
                for line in reader.lines() {
                    let extracted = extract_bytes(&line?, byte_pos.inner());
                    writeln!(writer, "{extracted}")?;
                }
            }
            Self::Chars(chars_pos) => {
                for line in reader.lines() {
                    let extracted = extract_chars(&line?, chars_pos.inner());
                    writeln!(writer, "{extracted}")?;
                }
            }
            Self::Fields(field_pos) => {
                let mut rdr = csv::ReaderBuilder::new()
                    .delimiter(delimiter)
                    .has_headers(false)
                    .from_reader(reader);
                let mut wrt = csv::WriterBuilder::new()
                    .delimiter(delimiter)
                    .from_writer(writer);

                for record in rdr.records() {
                    let extracted = extract_fields(&record?, field_pos.inner());
                    wrt.write_record(extracted)?;
                }
            }
        }
        Ok(())
    }
}

fn extract_chars(line: &str, char_pos: &[Range<usize>]) -> String {
    let chars: Vec<_> = line.chars().collect();
    char_pos
        .iter()
        .flat_map(|range| range.iter().filter_map(|i| chars.get(i)))
        .collect()
}

fn extract_bytes(line: &str, byte_pos: &[Range<usize>]) -> String {
    let bytes: Vec<_> = line.bytes().collect();
    let selected: Vec<_> = byte_pos
        .iter()
        .flat_map(|range| range.iter().filter_map(|i| bytes.get(i)).copied())
        .collect();
    String::from_utf8_lossy(&selected).into_owned()
}

fn extract_fields(record: &StringRecord, field_pos: &[Range<usize>]) -> Vec<String> {
    field_pos
        .iter()
        .flat_map(|range| range.iter().filter_map(|i| record.get(i).map(String::from)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn extract_chars_empty_string() {
        let res = extract_chars("", &[Range::from(0..1)]);
        let expected = "";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_chars_single_range() {
        let res = extract_chars("ébc", &[Range::from(0..1)]);
        let expected = "é";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_chars_two_ranges() {
        let res = extract_chars("ébc", &[Range::from(0..1), Range::from(2..3)]);
        let expected = "éc";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_chars_full_range() {
        let res = extract_chars("ébc", &[Range::from(0..3)]);
        let expected = "ébc";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_chars_two_unordered_ranges() {
        let res = extract_chars("ébc", &[Range::from(2..3), Range::from(1..2)]);
        let expected = "cb";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_chars_with_missing_range() {
        let res = extract_chars(
            "ébc",
            &[Range::from(0..1), Range::from(1..2), Range::from(4..5)],
        );
        let expected = "éb";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_empty_string() {
        let res = extract_bytes("", &[Range::from(0..1)]);
        let expected = "";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_single_range_unicode_split() {
        let res = extract_bytes("ébc", &[Range::from(0..1)]);
        let expected = "�";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_single_range() {
        let res = extract_bytes("ébc", &[Range::from(0..2)]);
        let expected = "é";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_two_chars() {
        let res = extract_bytes("ébc", &[Range::from(0..3)]);
        let expected = "éb";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_full_range() {
        let res = extract_bytes("ébc", &[Range::from(0..4)]);
        let expected = "ébc";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_two_ranges() {
        let res = extract_bytes("ébc", &[Range::from(3..4), Range::from(2..3)]);
        let expected = "cb";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_bytes_with_missing_range() {
        let res = extract_bytes("ébc", &[Range::from(0..2), Range::from(5..6)]);
        let expected = "é";
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_fields_single_range() {
        let input = StringRecord::from(vec!["Captain", "Sham", "12345"]);
        let res = extract_fields(&input, &[Range::from(0..1)]);
        let expected = &["Captain"];
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_fields_single_range_second() {
        let input = StringRecord::from(vec!["Captain", "Sham", "12345"]);
        let res = extract_fields(&input, &[Range::from(1..2)]);
        let expected = &["Sham"];
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_fields_two_ranges() {
        let input = StringRecord::from(vec!["Captain", "Sham", "12345"]);
        let res = extract_fields(&input, &[Range::from(0..1), Range::from(2..3)]);
        let expected = &["Captain", "12345"];
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_fields_with_missing_range() {
        let input = StringRecord::from(vec!["Captain", "Sham", "12345"]);
        let res = extract_fields(&input, &[Range::from(0..1), Range::from(3..4)]);
        let expected = &["Captain"];
        assert_eq!(res, expected);
    }

    #[test]
    fn extract_fields_unordered_ranges() {
        let input = StringRecord::from(vec!["Captain", "Sham", "12345"]);
        let res = extract_fields(&input, &[Range::from(1..2), Range::from(0..1)]);
        let expected = &["Sham", "Captain"];
        assert_eq!(res, expected);
    }
}
