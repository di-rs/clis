use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Month(u32);

impl Month {
    const MONTH_NAMES: [&str; 12] = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ];

    /// Create a month numbered 1–12.
    ///
    /// # Panics
    /// Debug builds panic if the value is outside 1–12.
    #[must_use]
    pub fn new(value: u32) -> Self {
        debug_assert!(value <= 12, "cannot be bigger than 12");
        debug_assert!(value > 0, "should be positive value");
        Self(value)
    }

    #[must_use]
    pub const fn next_month(&self) -> Self {
        let next_month = (self.0 % 12).saturating_add(1);
        Self(next_month)
    }

    #[must_use]
    pub const fn inner(&self) -> u32 {
        self.0
    }

    #[must_use]
    #[allow(
        clippy::indexing_slicing,
        clippy::as_conversions,
        reason = "Month values must be in 1..=12; their zero-based indices fit usize on supported platforms."
    )]
    pub fn get_name(&self) -> String {
        let num = self.0 as usize;
        Self::MONTH_NAMES[num.saturating_sub(1)].to_owned()
    }
}

#[derive(Debug, thiserror::Error)]
pub enum FromStrParseError {
    #[error("not in the range 1 through 12")]
    IncorrectNumber,
    #[error("invalid string provided")]
    InvalidMonthString,
    #[error(transparent)]
    IntParse(#[from] std::num::TryFromIntError),
}

impl FromStr for Month {
    type Err = FromStrParseError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if let Ok(num) = s.parse::<u32>() {
            match num {
                1..=12 => Ok(Self::new(num)),
                _ => Err(Self::Err::IncorrectNumber),
            }
        } else {
            let lower = &s.to_lowercase();
            let matches = Self::MONTH_NAMES
                .iter()
                .enumerate()
                .filter_map(|(i, name)| {
                    name.to_lowercase()
                        .starts_with(lower)
                        .then_some(i.saturating_add(1))
                })
                .collect::<Vec<_>>();
            if let [index] = matches.as_slice() {
                let num = u32::try_from(*index)?;
                Ok(Self::new(num))
            } else {
                Err(Self::Err::InvalidMonthString)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Month;

    #[test]
    fn next_month_keeps_valid_month_numbers() {
        for (number, next_number, next_name) in [(11, 12, "December"), (12, 1, "January")] {
            let next = Month::new(number).next_month();
            assert_eq!(next.inner(), next_number);
            assert_eq!(next.get_name(), next_name);
        }
    }
}
