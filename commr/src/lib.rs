use std::{cmp::Ordering, io::BufRead};

#[derive(Debug, Clone, Copy)]
pub enum Column<'a> {
    Col1(&'a str),
    Col2(&'a str),
    Col3(&'a str),
}

pub trait Reporter {
    fn report(&self, col: Column) -> ();
}

/// # Errors
/// Throws error if unable to write to writer
pub fn get_lines(
    reader1: impl BufRead,
    reader2: impl BufRead,
    insensitive: bool,
    reporter: &impl Reporter,
) {
    let reader1 = get_lines_reader(reader1, insensitive);
    let reader2 = get_lines_reader(reader2, insensitive);

    traverse_lines(reader1, reader2, reporter);
}

fn get_lines_reader(reader: impl BufRead, insensitive: bool) -> impl Iterator<Item = String> {
    reader.lines().map_while(Result::ok).map(move |line| {
        if insensitive {
            line.to_lowercase()
        } else {
            line
        }
    })
}

fn traverse_lines<R1, R2>(mut reader1: R1, mut reader2: R2, reporter: &impl Reporter)
where
    R1: Iterator<Item = String>,
    R2: Iterator<Item = String>,
{
    let mut line1 = reader1.next();
    let mut line2 = reader2.next();

    while line1.is_some() || line2.is_some() {
        match (&line1, &line2) {
            (Some(val1), Some(val2)) => match val1.cmp(val2) {
                Ordering::Equal => {
                    reporter.report(Column::Col3(val1));
                    line1 = reader1.next();
                    line2 = reader2.next();
                }
                Ordering::Less => {
                    reporter.report(Column::Col1(val1));
                    line1 = reader1.next();
                }
                Ordering::Greater => {
                    reporter.report(Column::Col2(val2));
                    line2 = reader2.next();
                }
            },
            (Some(val1), None) => {
                reporter.report(Column::Col1(val1));
                line1 = reader1.next();
            }
            (None, Some(val2)) => {
                reporter.report(Column::Col2(val2));
                line2 = reader2.next();
            }
            _ => (),
        }
    }
}
