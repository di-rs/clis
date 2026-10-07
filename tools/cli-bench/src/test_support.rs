pub use crate as bench_api;
#[path = "../tests/common/build.rs"]
pub mod build;
#[path = "../tests/common/dataset.rs"]
pub mod dataset_support;
#[path = "../tests/common/gate.rs"]
pub mod gate_support;
#[path = "../tests/common/rss.rs"]
pub mod rss_support;
#[path = "../tests/common/timing.rs"]
pub mod timing_support;
#[path = "../tests/common/validation.rs"]
pub mod validation_support;

pub struct TestLock {
    guard: crate::MeasurementLock,
    _root: assert_fs::TempDir,
}
impl std::ops::Deref for TestLock {
    type Target = crate::MeasurementLock;
    fn deref(&self) -> &Self::Target {
        &self.guard
    }
}
pub fn measurement_lock() -> Result<TestLock, Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let guard = crate::MeasurementLock::acquire_in_test_directory(
        &root.join("lock"),
        &std::sync::atomic::AtomicBool::new(false),
        || Ok(()),
    )?;
    Ok(TestLock { guard, _root: root })
}
pub fn validation_fixture(
    candidate: &str,
    previous: &str,
) -> Result<validation_support::Fixture, Box<dyn std::error::Error>> {
    let root = assert_fs::TempDir::new()?;
    let guard = crate::MeasurementLock::acquire_in_test_directory(
        &root.join("lock"),
        &std::sync::atomic::AtomicBool::new(false),
        || Ok(()),
    )?;
    validation_support::Fixture::from_root_and_lock(root, guard, candidate, previous)
}
pub fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

pub fn equal<T: PartialEq<U> + std::fmt::Debug, U: std::fmt::Debug>(
    actual: &T,
    expected: &U,
) -> Result<(), Box<dyn std::error::Error>> {
    if actual == expected {
        Ok(())
    } else {
        Err(format!("expected {expected:?}, observed {actual:?}").into())
    }
}
