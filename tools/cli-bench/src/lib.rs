#![doc = include_str!("../README.md")]
#![forbid(unsafe_code)]

mod error;
mod model;
mod suite;

pub use error::{BenchError, ErrorKind};
pub use model::*;
pub use suite::{parse_suite, validate_suite};

mod artifact;
mod host;
mod store;
pub use artifact::{fingerprint, register_binary, verify_file};
pub use host::collect_host;
pub use store::{RunBundle, RunWriter, Store};

mod process;
pub use process::{
    CapturePaths, CommandInput, CommandOutput, CommandSpec, ExecutionPolicy, OutputFileLimit,
    ProcessOutcome, ProcessRunner, ProcessStatus, StopReason,
};

mod invocation;
pub use invocation::{
    BoundExecutable, BoundTool, DatasetSet, Invocation, InvocationScope, PipelineTools,
    RoleBindings, resolve_invocation,
};

mod build;
pub use build::{
    BuildRequest, BuildTools, ExecutableSource, GitContext, ResolvedRevision, RunRequest,
    bind_roles, build_revision, resolve_revision,
};

mod dataset;

pub use dataset::{DatasetPreparation, prepare_datasets, verify_datasets};

mod sandbox;
pub use sandbox::{CaseId, OwnedScratch, create_scratch, reset_case};

mod check;
pub use check::{
    ExperimentPreparation, PreparedExperiment, ValidatedExperiment, ValidationFinding,
    ValidationObservation, ValidationReport, prepare_experiment, validate_experiment,
};
