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
