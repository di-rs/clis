use chrono::{Local, NaiveDate};

mod month;
pub use month::Month;

#[must_use]
pub fn get_today() -> NaiveDate {
    Local::now().date_naive()
}