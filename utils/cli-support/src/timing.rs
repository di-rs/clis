use crate::{Format, sink::Sink};
use serde_json::{Map, Value};
use std::{
    fmt::{self, Write as _},
    io::Write,
    time::Instant,
};
use tracing::{
    Subscriber,
    field::{Field, Visit},
    span::{Attributes, Id, Record},
};
use tracing_subscriber::{Layer, layer::Context, registry::LookupSpan};

pub struct Timings {
    pub sink: Sink,
    pub format: Format,
}

struct Stage {
    started: Instant,
    fields: Fields,
}

#[derive(Default)]
struct Fields(Map<String, Value>);

impl Visit for Fields {
    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        self.0
            .insert(field.name().into(), Value::String(format!("{value:?}")));
    }
    fn record_str(&mut self, field: &Field, value: &str) {
        self.0
            .insert(field.name().into(), Value::String(value.into()));
    }
    fn record_u64(&mut self, field: &Field, value: u64) {
        self.0.insert(field.name().into(), value.into());
    }
    fn record_i64(&mut self, field: &Field, value: i64) {
        self.0.insert(field.name().into(), value.into());
    }
    fn record_bool(&mut self, field: &Field, value: bool) {
        self.0.insert(field.name().into(), value.into());
    }
    fn record_f64(&mut self, field: &Field, value: f64) {
        self.0.insert(field.name().into(), value.into());
    }
}

impl<S> Layer<S> for Timings
where
    S: Subscriber + for<'lookup> LookupSpan<'lookup>,
{
    fn on_new_span(&self, attributes: &Attributes<'_>, id: &Id, context: Context<'_, S>) {
        if let Some(span) = context.span(id) {
            let mut stage = Stage {
                started: Instant::now(),
                fields: Fields::default(),
            };
            attributes.record(&mut stage.fields);
            span.extensions_mut().insert(stage);
        }
    }

    fn on_record(&self, id: &Id, values: &Record<'_>, context: Context<'_, S>) {
        if let Some(span) = context.span(id)
            && let Some(stage) = span.extensions_mut().get_mut::<Stage>()
        {
            values.record(&mut stage.fields);
        }
    }

    fn on_close(&self, id: Id, context: Context<'_, S>) {
        let Some(span) = context.span(&id) else {
            return;
        };
        let Some(stage) = span.extensions_mut().remove::<Stage>() else {
            return;
        };
        let elapsed_ms = stage.started.elapsed().as_secs_f64() * 1000.0;
        let record = match self.format {
            Format::Json => serde_json::json!({
                "kind": "timing", "stage": span.name(),
                "elapsed_ms": elapsed_ms, "fields": stage.fields.0,
            })
            .to_string(),
            Format::Text => {
                let mut fields = String::new();
                for (key, value) in &stage.fields.0 {
                    let _ = write!(fields, " {key}={value}");
                }
                format!(
                    "TIMING stage={} elapsed_ms={elapsed_ms:.3}{fields}",
                    span.name()
                )
            }
        };
        // Sink latches failures; the CLI observes them through Runtime::finish.
        let _ = self
            .sink
            .clone()
            .write_all(format!("{record}\n").as_bytes());
    }
}
