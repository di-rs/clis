//! Private logical run-evidence accounting; never a filesystem quota.
use crate::BenchError;
use std::{
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};

pub const FAILURE_METADATA_BYTES: u64 = 16_777_216;
#[derive(Clone, Debug)]
pub struct EvidenceBudget {
    root: PathBuf,
    limit: u64,
    failed: Arc<AtomicBool>,
    failure_remaining: Arc<AtomicU64>,
}
impl EvidenceBudget {
    pub fn new(root: PathBuf, limit: u64) -> Self {
        Self {
            root,
            limit,
            failed: Arc::new(AtomicBool::new(false)),
            failure_remaining: Arc::new(AtomicU64::new(FAILURE_METADATA_BYTES)),
        }
    }
    pub fn contains(&self, path: &Path) -> bool {
        path.starts_with(&self.root)
    }
    pub fn is_failed(&self) -> bool {
        self.failed.load(Ordering::Relaxed)
    }
    pub fn fail(&self) {
        self.failed.store(true, Ordering::Relaxed);
    }
    pub fn remaining(&self) -> Result<u64, BenchError> {
        if self.failed.load(Ordering::Relaxed) {
            return Err(exhausted(
                "run evidence failure mode prohibits new children",
            ));
        }
        self.limit.checked_sub(self.normal_size()?).ok_or_else(|| {
            exhausted("run evidence exceeds max_evidence_bytes; retained for diagnosis")
        })
    }
    fn normal_size(&self) -> Result<u64, BenchError> {
        let mut total = size(&self.root)?;
        // The deterministic checksum index has its own approved exception.
        for name in ["checksums.json", "checksums.pending"] {
            if let Ok(metadata) = self.root.join(name).symlink_metadata() {
                total = total.saturating_sub(metadata.len());
            }
        }
        Ok(total)
    }
    pub fn ensure_capacity(&self, bytes: usize) -> Result<(), BenchError> {
        let remaining = if self.failed.load(Ordering::Relaxed) {
            self.failure_remaining.load(Ordering::Relaxed)
        } else {
            self.remaining()?
        };
        if u64::try_from(bytes).map_or(true, |bytes| bytes > remaining) {
            return Err(exhausted(
                "run evidence metadata exceeds remaining allowance",
            ));
        }
        Ok(())
    }
    pub fn reserve_write(&self, bytes: usize) -> Result<(), BenchError> {
        let bytes = u64::try_from(bytes).map_err(|_| exhausted("evidence size overflow"))?;
        if self.failed.load(Ordering::Relaxed) {
            self.failure_remaining
                .try_update(Ordering::Relaxed, Ordering::Relaxed, |left| {
                    left.checked_sub(bytes)
                })
                .map_err(|_| exhausted("failure metadata exceeds separate 16 MiB allowance"))?;
        } else if bytes > self.remaining()? {
            return Err(exhausted(
                "normal evidence write would exceed max_evidence_bytes",
            ));
        }
        Ok(())
    }
}
/// Shared by both retained capture streams and the monitor's external-file deltas.
#[derive(Debug)]
pub struct CaptureAllowance {
    remaining: AtomicU64,
    pub exceeded: AtomicBool,
}
impl CaptureAllowance {
    pub const fn new(bytes: u64) -> Self {
        Self {
            remaining: AtomicU64::new(bytes),
            exceeded: AtomicBool::new(false),
        }
    }
    pub fn claim(&self, requested: u64) -> u64 {
        let before = self
            .remaining
            .try_update(Ordering::Relaxed, Ordering::Relaxed, |left| {
                Some(left.saturating_sub(requested))
            })
            .unwrap_or_else(|left| left);
        let granted = before.min(requested);
        if granted < requested {
            self.exceeded.store(true, Ordering::Relaxed);
        }
        granted
    }
}

pub fn size(path: &Path) -> Result<u64, BenchError> {
    let mut total = 0_u64;
    for entry in std::fs::read_dir(path)? {
        let path = entry?.path();
        let metadata = path.symlink_metadata()?;
        let bytes = if metadata.is_dir() {
            size(&path)?
        } else if metadata.is_file() {
            metadata.len()
        } else {
            return Err(exhausted("non-regular entry in run evidence"));
        };
        total = total
            .checked_add(bytes)
            .ok_or_else(|| exhausted("evidence size overflow"))?;
    }
    Ok(total)
}
pub fn sum_sizes(parts: &[usize]) -> Result<usize, BenchError> {
    parts.iter().try_fold(0_usize, |sum, part| {
        sum.checked_add(*part)
            .ok_or_else(|| exhausted("evidence size overflow"))
    })
}
fn exhausted(message: &str) -> BenchError {
    BenchError::Evidence(message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn normal_metadata_is_bounded_before_writing_and_failure_reserve_is_separate()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        std::fs::write(root.join("retained"), [0; 90])?;
        let budget = EvidenceBudget::new(root.path().into(), 100);
        crate::test_support::equal(&budget.remaining()?, &10)?;
        budget.reserve_write(10)?;
        crate::test_support::require(
            budget.reserve_write(11).is_err(),
            "metadata exceeded normal budget",
        )?;
        std::fs::write(root.join("external-overshoot"), [0; 20])?;
        crate::test_support::require(
            budget.remaining().is_err(),
            "external overshoot allowed new work",
        )?;
        budget.fail();
        budget.reserve_write(usize::try_from(FAILURE_METADATA_BYTES)?)?;
        crate::test_support::require(
            budget.reserve_write(1).is_err(),
            "failure envelope exceeded",
        )?;
        crate::test_support::require(
            budget.remaining().is_err(),
            "failure mode allowed another child",
        )?;
        Ok(())
    }
    #[test]
    fn metadata_inventory_overhead_is_recorded_without_a_timing_gate()
    -> Result<(), Box<dyn std::error::Error>> {
        let root = assert_fs::TempDir::new()?;
        for index in 0..1_000 {
            std::fs::write(root.join(format!("capture-{index}")), [0; 16])?;
        }
        let budget = EvidenceBudget::new(root.path().into(), 1_000_000);
        let started = std::time::Instant::now();
        for _ in 0..100 {
            crate::test_support::equal(&budget.remaining()?, &984_000)?;
        }
        eprintln!(
            "metadata-only inventory: 100 scans x 1000 files, elapsed {:?}; outside child intervals",
            started.elapsed()
        );
        Ok(())
    }
}
