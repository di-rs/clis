use std::{range::Range, str::FromStr};

#[derive(Debug)]
pub struct PositionList(Vec<Range<usize>>);

impl PositionList {
    pub(crate) const fn inner(&self) -> &Vec<Range<usize>> {
        &self.0
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ParseError {
    #[error("illegal list value: `{0}`")]
    IncorrectValue(String),
    #[error("first number in range ({0}) must be lower than second number ({1})")]
    IncorrectRange(String, String),
    #[error("wonky incorrect range provided")]
    WonkyRange,
}

impl FromStr for PositionList {
    type Err = ParseError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let mut res = Vec::new();
        let raw_ranges = s.split(',');

        for range in raw_ranges {
            let range_split = range.split_once('-');

            let parsed_range = if let Some((start, end)) = range_split {
                let range_start = Self::parse_range_part(start)?;
                let range_end = Self::parse_range_part(end)?;
                if range_end <= range_start {
                    return Err(ParseError::IncorrectRange(start.to_owned(), end.to_owned()));
                }
                (range_start, range_end)
            } else {
                let range_end = Self::parse_range_part(range)?;
                (range_end, range_end)
            };

            res.push(Range::from(
                parsed_range.0.saturating_sub(1)..parsed_range.1,
            ));
        }

        Ok(Self(res))
    }
}

impl PositionList {
    fn parse_range_part(value: &str) -> Result<usize, ParseError> {
        if value.starts_with('+') {
            return Err(ParseError::IncorrectValue(value.to_owned()));
        }
        let parsed =
            usize::from_str(value).map_err(|_| ParseError::IncorrectValue(value.to_owned()))?;
        if parsed == 0 {
            return Err(ParseError::IncorrectValue(value.to_owned()));
        }
        Ok(parsed)
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn parse_empty_pos() {
        assert!(PositionList::from_str("").is_err());
    }

    #[test]
    fn parse_zero_pos() {
        let res = PositionList::from_str("0");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `0`".to_string()
        );
    }

    #[test]
    fn parse_zero_range_pos() {
        let res = PositionList::from_str("0-1");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `0`".to_string()
        );
    }

    #[test]
    fn parse_leading_plus_pos() {
        let res = PositionList::from_str("+1");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `+1`".to_string()
        );
    }

    #[test]
    fn parse_leading_plus_range_pos() {
        let res = PositionList::from_str("+1-2");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `+1`".to_string()
        );
    }

    #[test]
    fn parse_end_plus_range_pos() {
        let res = PositionList::from_str("1-+2");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `+2`".to_string()
        );
    }

    #[test]
    fn parse_non_number_pos() {
        let res = PositionList::from_str("a");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `a`".to_string()
        );
    }

    #[test]
    fn parse_non_number_coma_pos() {
        let res = PositionList::from_str("1,a");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `a`".to_string()
        );
    }

    #[test]
    fn parse_non_number_range_pos() {
        let res = PositionList::from_str("1-a");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `a`".to_string()
        );
    }

    #[test]
    fn parse_non_number_leading_range_pos() {
        let res = PositionList::from_str("a-1");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "illegal list value: `a`".to_string()
        );
    }

    #[test]
    fn parse_wonky_empty_range() {
        let res = PositionList::from_str("-");
        assert!(res.is_err());
    }

    #[test]
    fn parse_wonky_comma_range() {
        let res = PositionList::from_str(",");
        assert!(res.is_err());
    }

    #[test]
    fn parse_wonky_comma_end_range() {
        let res = PositionList::from_str("1,");
        assert!(res.is_err());
    }

    #[test]
    fn parse_wonky_leading_range() {
        let res = PositionList::from_str("1-");
        assert!(res.is_err());
    }

    #[test]
    fn parse_wonky_incorrect_count_range() {
        let res = PositionList::from_str("1-1-1");
        assert!(res.is_err());
    }

    #[test]
    fn parse_wonky_incorrect_count_symbol_range() {
        let res = PositionList::from_str("1-1-a");
        assert!(res.is_err());
    }

    #[test]
    fn parse_leading_range_number_equals_end() {
        let res = PositionList::from_str("1-1");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "first number in range (1) must be lower than second number (1)"
        );
    }

    #[test]
    fn parse_leading_range_number_bigger_end() {
        let res = PositionList::from_str("2-1");
        assert!(res.is_err());
        assert_eq!(
            res.unwrap_err().to_string(),
            "first number in range (2) must be lower than second number (1)"
        );
    }

    #[test]
    fn parse_single_value_range() {
        let res = PositionList::from_str("1");
        let expected = vec![Range::from(0..1)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_trailing_zero_single_value_range() {
        let res = PositionList::from_str("01");
        let expected = vec![Range::from(0..1)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_coma_separated_ranges() {
        let res = PositionList::from_str("1,3");
        let expected = vec![Range::from(0..1), Range::from(2..3)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_coma_separated_trailing_zero_ranges() {
        let res = PositionList::from_str("001,0003");
        let expected = vec![Range::from(0..1), Range::from(2..3)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_single_range() {
        let res = PositionList::from_str("1-3");
        let expected = vec![Range::from(0..3)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_trailing_zero_single_range() {
        let res = PositionList::from_str("0001-03");
        let expected = vec![Range::from(0..3)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_multiple_ranges() {
        let res = PositionList::from_str("1,7,3-5");
        let expected = vec![Range::from(0..1), Range::from(6..7), Range::from(2..5)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }

    #[test]
    fn parse_two_digit_ranges() {
        let res = PositionList::from_str("15,19-20");
        let expected = vec![Range::from(14..15), Range::from(18..20)];
        assert!(res.is_ok());
        assert_eq!(res.unwrap().0, expected);
    }
}
