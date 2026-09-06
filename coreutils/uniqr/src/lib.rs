use std::collections::VecDeque;
use std::io::{BufRead, Write};

pub struct LineNode {
    inner: String,
    count: usize,
}

impl LineNode {
    fn new(line: &str) -> Self {
        Self {
            inner: line.to_owned(),
            count: 1,
        }
    }

    const fn increase(&mut self) {
        self.count = self.count.saturating_add(1);
    }
}

impl PartialEq for LineNode {
    fn eq(&self, other: &Self) -> bool {
        self.inner.trim_end() == other.inner.trim_end()
    }
}

pub struct UniqueList(VecDeque<LineNode>);

impl UniqueList {
    /// # Errors
    /// Throws error if unable to read the line
    pub fn from_reader(mut reader: impl BufRead) -> Result<Self, std::io::Error> {
        let mut list = VecDeque::<LineNode>::new();
        let mut line = String::new();

        while let bytes = reader.read_line(&mut line)?
            && bytes != 0
        {
            let prev = list.back_mut();
            let cur = LineNode::new(&line);

            if let Some(prev) = prev
                && *prev == cur
            {
                prev.increase();
            } else {
                list.push_back(cur);
            }

            line.clear();
        }

        Ok(Self(list))
    }
}

/// # Errors
/// Throws error if unable to write into writer
pub fn report_unique_lines(
    mut writer: impl Write,
    list: UniqueList,
    line_count: bool,
) -> Result<(), std::io::Error> {
    for item in list.0 {
        let LineNode { inner, count } = item;

        if line_count {
            write!(writer, "{count:>4} {inner}")?;
        } else {
            write!(writer, "{inner}")?;
        }
    }

    Ok(())
}
