use chrono::{Local, NaiveDate};
use std::io::Write;

mod month;
pub use month::Month;

pub struct Date {
    year: i32,
    month: Month,
    day: NaiveDate,
}

impl Date {
    #[must_use]
    pub const fn new(year: i32, month: Month, day: NaiveDate) -> Self {
        Self { year, month, day }
    }

    fn last_day_in_month(&self) -> NaiveDate {
        let next_month = self.month.next_month();
        let is_next_year = next_month.inner() == 1;
        let year = self.year.saturating_add(i32::from(is_next_year));

        NaiveDate::from_ymd_opt(year, next_month.inner(), 1)
            .unwrap_or_default()
            .pred_opt()
            .unwrap_or_default()
    }
}

#[must_use]
pub fn get_today() -> NaiveDate {
    Local::now().date_naive()
}

pub fn format_month(mut writer: impl Write, date: &Date) {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    #[allow(clippy::unwrap_used)]
    fn format_month_leap_year() {
        let mut buf = Vec::new();
        let today = NaiveDate::from_ymd_opt(0, 1, 1).unwrap();
        let date = Date::new(2020, Month::new(2), today);

        let leap_february = [
            "   February 2020      ",
            "Su Mo Tu We Th Fr Sa  ",
            "                   1  ",
            " 2  3  4  5  6  7  8  ",
            " 9 10 11 12 13 14 15  ",
            "16 17 18 19 20 21 22  ",
            "23 24 25 26 27 28 29  ",
            "                      ",
        ];

        format_month(&mut buf, &date);
        assert_eq!(buf, leap_february.join("\n").as_bytes());
    }

    #[test]
    #[allow(clippy::unwrap_used)]
    fn test_format_month_selected_day() {
        let mut buf = Vec::new();
        let today = NaiveDate::from_ymd_opt(2021, 4, 7).unwrap();
        let date = Date::new(2020, Month::new(2), today);

        let april_hl = [
            "     April 2021       ",
            "Su Mo Tu We Th Fr Sa  ",
            "             1  2  3  ",
            " 4  5  6 \u{1b}[7m 7\u{1b}[0m  8  9 10  ",
            "11 12 13 14 15 16 17  ",
            "18 19 20 21 22 23 24  ",
            "25 26 27 28 29 30     ",
            "                      ",
        ];

        format_month(&mut buf, &date);
        assert_eq!(buf, april_hl.join("\n").as_bytes());
    }
}
