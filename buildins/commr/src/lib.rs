use std::{cmp::Ordering, io::BufRead};

#[derive(Debug, Clone, Copy)]
pub enum Column<'a> {
    Col1(&'a str),
    Col2(&'a str),
    Col3(&'a str),
}

pub trait Reporter {
    /// # Errors
    /// Returns an error when the selected column cannot be written.
    fn report(&self, col: Column) -> std::io::Result<()>;
}

/// # Errors
/// Returns decoding, input I/O, or reporter output errors
pub fn get_lines(
    reader1: impl BufRead,
    reader2: impl BufRead,
    insensitive: bool,
    reporter: &impl Reporter,
) -> std::io::Result<()> {
    let reader1 = get_lines_reader(reader1, insensitive);
    let reader2 = get_lines_reader(reader2, insensitive);

    traverse_lines(reader1, reader2, reporter)
}

fn get_lines_reader(
    reader: impl BufRead,
    insensitive: bool,
) -> impl Iterator<Item = std::io::Result<String>> {
    reader.lines().map(move |line| {
        line.map(|line| {
            if insensitive {
                line.to_lowercase()
            } else {
                line
            }
        })
    })
}

fn traverse_lines<R1, R2>(
    mut reader1: R1,
    mut reader2: R2,
    reporter: &impl Reporter,
) -> std::io::Result<()>
where
    R1: Iterator<Item = std::io::Result<String>>,
    R2: Iterator<Item = std::io::Result<String>>,
{
    let mut line1 = reader1.next().transpose()?;
    let mut line2 = reader2.next().transpose()?;

    while line1.is_some() || line2.is_some() {
        match (&line1, &line2) {
            (Some(val1), Some(val2)) => match val1.cmp(val2) {
                Ordering::Equal => {
                    reporter.report(Column::Col3(val1))?;
                    line1 = reader1.next().transpose()?;
                    line2 = reader2.next().transpose()?;
                }
                Ordering::Less => {
                    reporter.report(Column::Col1(val1))?;
                    line1 = reader1.next().transpose()?;
                }
                Ordering::Greater => {
                    reporter.report(Column::Col2(val2))?;
                    line2 = reader2.next().transpose()?;
                }
            },
            (Some(val1), None) => {
                reporter.report(Column::Col1(val1))?;
                line1 = reader1.next().transpose()?;
            }
            (None, Some(val2)) => {
                reporter.report(Column::Col2(val2))?;
                line2 = reader2.next().transpose()?;
            }
            _ => (),
        }
    }
    Ok(())
}
