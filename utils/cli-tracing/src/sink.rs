use std::io::{self, Write};
use std::sync::{Arc, Mutex, MutexGuard};

struct State {
    writer: Box<dyn Write + Send>,
    failure: Option<io::Error>,
}

/// Serializes complete records and preserves the first sink failure for finish.
#[derive(Clone)]
pub struct Sink(Arc<Mutex<State>>);

impl Sink {
    pub fn new(writer: impl Write + Send + 'static) -> Self {
        Self(Arc::new(Mutex::new(State {
            writer: Box::new(writer),
            failure: None,
        })))
    }

    fn lock(&self) -> io::Result<MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("diagnostic sink lock poisoned"))
    }

    pub fn finish(&self) -> io::Result<()> {
        let mut state = self.lock()?;
        let result = state.writer.flush();
        state.remember(result)
    }
}

impl State {
    fn remember(&mut self, result: io::Result<()>) -> io::Result<()> {
        if self.failure.is_none() {
            self.failure = result.err();
        }
        self.failure.as_ref().map_or(Ok(()), |error| {
            Err(io::Error::new(error.kind(), error.to_string()))
        })
    }
}

impl Write for Sink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let mut state = self.lock()?;
        state.remember(Ok(()))?;
        let result = state.writer.write_all(bytes);
        state.remember(result)?;
        drop(state);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        self.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FailingWriter {
        on_flush: bool,
    }
    impl Write for FailingWriter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            if self.on_flush {
                Ok(bytes.len())
            } else {
                Err(io::ErrorKind::BrokenPipe.into())
            }
        }
        fn flush(&mut self) -> io::Result<()> {
            Err(io::ErrorKind::WriteZero.into())
        }
    }

    #[test]
    fn finish_retains_write_errors_swallowed_by_the_formatter() {
        let mut sink = Sink::new(FailingWriter { on_flush: false });
        assert!(sink.write_all(b"record\n").is_err());
        assert!(
            sink.finish()
                .is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe)
        );
        assert!(
            sink.finish()
                .is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe)
        );
    }

    #[test]
    fn finish_reports_flush_failure_after_successful_writes() {
        let mut sink = Sink::new(FailingWriter { on_flush: true });
        assert!(sink.write_all(b"record\n").is_ok());
        assert!(
            sink.finish()
                .is_err_and(|error| error.kind() == io::ErrorKind::WriteZero)
        );
    }
}
