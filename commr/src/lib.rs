use std::{cmp::Ordering, io::BufRead};

#[derive(Debug, Clone, Copy)]
pub enum Column<'a> {
    Col1(&'a str),
    Col2(&'a str),
    Col3(&'a str),
}

/// # Errors
/// Throws error if unable to write to writer
pub fn get_lines<F>(reader1: impl BufRead, reader2: impl BufRead, insensitive: bool, cb: F)
where
    F: FnMut(Column),
{
    let reader1 = get_lines_reader(reader1, insensitive);
    let reader2 = get_lines_reader(reader2, insensitive);

    traverse_lines(reader1, reader2, cb);
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

fn traverse_lines<F, R1, R2>(mut reader1: R1, mut reader2: R2, mut cb: F)
where
    F: FnMut(Column),
    R1: Iterator<Item = String>,
    R2: Iterator<Item = String>,
{
    let mut line1 = reader1.next();
    let mut line2 = reader2.next();

    while line1.is_some() || line2.is_some() {
        match (&line1, &line2) {
            (Some(val1), Some(val2)) => match val1.cmp(val2) {
                Ordering::Equal => {
                    cb(Column::Col3(val1));
                    line1 = reader1.next();
                    line2 = reader2.next();
                }
                Ordering::Less => {
                    cb(Column::Col1(val1));
                    line1 = reader1.next();
                }
                Ordering::Greater => {
                    cb(Column::Col2(val2));
                    line2 = reader2.next();
                }
            },
            (Some(val1), None) => {
                cb(Column::Col1(val1));
                line1 = reader1.next();
            }
            (None, Some(val2)) => {
                cb(Column::Col2(val2));
                line2 = reader2.next();
            }
            _ => (),
        }
    }
}
