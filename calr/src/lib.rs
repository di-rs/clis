use ansi_term::Style;
use chrono::{Datelike, Local, NaiveDate};

mod month;
pub use month::Month;

pub struct CalendarDate {
    year: i32,
    month: Month,
    today: NaiveDate,
}

impl CalendarDate {
    #[must_use]
    pub const fn new(year: i32, month: Month, today: NaiveDate) -> Self {
        Self { year, month, today }
    }

    #[allow(clippy::unwrap_used)]
    const fn first_day(&self) -> NaiveDate {
        NaiveDate::from_ymd_opt(self.year, self.month.inner(), 1).unwrap()
    }

    #[allow(clippy::unwrap_used)]
    const fn last_day_in_month(&self) -> NaiveDate {
        let month = self.month.inner();
        let (y, m) = if month == 12 {
            (self.year.saturating_add(1), 1)
        } else {
            (self.year, month.saturating_add(1))
        };

        NaiveDate::from_ymd_opt(y, m, 1)
            .unwrap()
            .pred_opt()
            .unwrap()
    }

    fn is_today(&self, day: u32) -> bool {
        self.year == self.today.year()
            && self.month.inner() == self.today.month()
            && day == self.today.day()
    }
}

#[must_use]
pub fn get_today() -> NaiveDate {
    Local::now().date_naive()
}

const LINE_WIDTH: usize = 22;

#[must_use]
pub fn format_month(date: &CalendarDate, print_year: bool) -> Vec<String> {
    let first = date.first_day();
    let mut days: Vec<String> = (1..first.weekday().number_from_sunday())
        .map(|_| "  ".to_string())
        .collect();

    let last = date.last_day_in_month();
    days.extend((first.day()..=last.day()).map(|num| {
        let fmt = format!("{num:>2}");
        if date.is_today(num) {
            Style::new().reverse().paint(fmt).to_string()
        } else {
            fmt
        }
    }));

    let mut lines = Vec::new();

    let month_name = date.month.get_name();
    let year = date.year;
    lines.push(format!(
        "{:^20}  ",
        if print_year {
            format!("{month_name} {year}")
        } else {
            month_name
        }
    ));

    lines.push("Su Mo Tu We Th Fr Sa  ".to_string());

    for week in days.chunks(7) {
        lines.push(format!(
            "{:width$}  ",
            week.join(" "),
            width = LINE_WIDTH - 2
        ));
    }

    while lines.len() < 8 {
        lines.push(" ".repeat(LINE_WIDTH));
    }

    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    #[allow(clippy::unwrap_used)]
    fn format_month_leap_year() {
        let today = NaiveDate::from_ymd_opt(0, 1, 1).unwrap();
        let date = CalendarDate::new(2020, Month::new(2), today);

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

        let buf = format_month(&date, true);
        assert_eq!(buf.join("\n"), leap_february.join("\n"));
    }

    #[test]
    #[allow(clippy::unwrap_used)]
    fn test_format_month_selected_day() {
        let today = NaiveDate::from_ymd_opt(2021, 4, 7).unwrap();
        let date = CalendarDate::new(2021, Month::new(4), today);

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

        let buf = format_month(&date, true);
        assert_eq!(buf.join("\n"), april_hl.join("\n"));
    }
}
