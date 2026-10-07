use super::bench_api;
use bench_api::MeasurementLock;
use std::sync::atomic::AtomicBool;
#[path = "validation.rs"]
mod shared;
pub use shared::*;

impl Fixture {
    pub fn new(candidate: &str, previous: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let measurement_lock = MeasurementLock::acquire(&AtomicBool::new(false), || Ok(()))?;
        Self::from_root_and_lock(
            assert_fs::TempDir::new()?,
            measurement_lock,
            candidate,
            previous,
        )
    }
}
