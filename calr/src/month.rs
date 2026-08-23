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

    #[must_use]
    pub fn new(value: u32) -> Self {
        debug_assert!(value <= 12, "cannot be bigger than 12");
        debug_assert!(value > 0, "should be positive value");
        Self(value)
    }

    #[must_use]
    pub const fn next_month(&self) -> Self {
        let next_month = (self.0.saturating_add(1)) % 12;
        Self(next_month)
    }

    #[must_use]
    pub const fn inner(&self) -> u32 {
        self.0
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
            if matches.len() == 1 {
                #[allow(clippy::indexing_slicing)]
                let num = u32::try_from(matches[0])?;
                Ok(Self::new(num))
            } else {
                Err(Self::Err::InvalidMonthString)
            }
        }
    }
}
