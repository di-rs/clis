use crate::{DatasetIdentity, DatasetSet, GenerationRecord, MeasurementProfile, RoleBindings};

/// Explicit profile, child resources and optional immutable expectations for replay.
#[derive(Debug)]
pub struct DatasetPreparation<'a> {
    pub profile: MeasurementProfile,
    pub bindings: &'a RoleBindings,
    pub expected: Option<&'a DatasetSet>,
}

/// Prepare independently validated Biggie inputs outside measurement.
///
/// # Errors
/// Rejects invalid recipes, quota overflow, changed identities, generation failures,
/// unsafe outputs and shape/hash mismatches; failed diagnostics remain in the store.
pub fn prepare_datasets(
    suite: &crate::Suite,
    request: &DatasetPreparation<'_>,
    store: &crate::Store,
    runner: &crate::ProcessRunner,
) -> Result<DatasetSet, BenchError> {
    crate::validate_suite(suite)?;
    let sizes = preflight_sizes(suite, request.profile)?;
    crate::invocation::directory(&request.bindings.home)?;
    crate::invocation::directory(&request.bindings.config)?;
    let environment = crate::invocation::child_environment(request.bindings)?;
    let generator = request
        .bindings
        .generator
        .as_ref()
        .ok_or_else(|| shape_error("missing bound Biggie generator"))?;
    store.verify_artifact(&generator.artifact)?;
    crate::verify_file(&generator.path, &generator.artifact.file)?;
    let identities: Vec<_> = suite
        .datasets
        .iter()
        .map(|spec| recipe_identity(suite, spec, request, &generator.artifact, &environment))
        .collect();
    if let Some(expected) = request.expected {
        validate_expected(expected, &identities)?;
    }
    let parent = store.root().join("datasets");
    crate::invocation::directory(&parent)?;
    let mut datasets = DatasetSet::default();
    let mut used = 0_u64;
    for (identity, bytes) in identities.iter().zip(sizes) {
        let expected = request
            .expected
            .and_then(|set| set.generations.get(&identity.dataset));
        let remaining = suite
            .limits
            .max_generated_bytes
            .checked_sub(used)
            .ok_or_else(|| shape_error("generated input budget exceeded"))?;
        let limit = bytes
            .min(suite.limits.max_generated_file_bytes)
            .min(remaining);
        let (input, record) = prepare_one(
            identity,
            expected,
            generator,
            &environment,
            store,
            runner,
            limit,
        )?;
        used = checked(used.checked_add(input.file.bytes))?;
        datasets.inputs.insert(identity.dataset.clone(), input);
        datasets
            .generations
            .insert(identity.dataset.clone(), record);
    }
    verify_datasets(&datasets)?;
    Ok(datasets)
}

fn preflight_sizes(
    suite: &crate::Suite,
    profile: MeasurementProfile,
) -> Result<Vec<u64>, BenchError> {
    let mut total = 0_u64;
    let mut sizes = Vec::new();
    for dataset in &suite.datasets {
        let checks = dataset
            .profiles
            .get(&profile)
            .map_or(&dataset.checks, |recipe| &recipe.checks);
        let bytes = declared_bytes(checks)?;
        total = total
            .checked_add(bytes)
            .ok_or_else(|| shape_error("generated input budget arithmetic overflow"))?;
        if bytes > suite.limits.max_generated_file_bytes || total > suite.limits.max_generated_bytes
        {
            return Err(shape_error(
                "selected dataset profile exceeds generated input budget",
            ));
        }
        sizes.push(bytes);
    }
    Ok(sizes)
}
fn recipe_identity(
    suite: &crate::Suite,
    spec: &crate::DatasetSpec,
    request: &DatasetPreparation<'_>,
    generator: &crate::ArtifactRecord,
    environment: &std::collections::BTreeMap<String, String>,
) -> DatasetIdentity {
    let recipe = spec
        .profiles
        .get(&request.profile)
        .cloned()
        .unwrap_or_else(|| crate::DatasetRecipe {
            argv: spec.argv.clone(),
            checks: spec.checks.clone(),
        });
    let token = format!("@records:{}", spec.id);
    let path_records = suite.cases.iter().any(|case| {
        let overrides = case.profiles.get(&request.profile);
        let arguments = overrides
            .and_then(|p| p.argv.as_ref())
            .unwrap_or(&case.argv);
        let roles = overrides
            .and_then(|p| p.role_argv.as_ref())
            .unwrap_or(&case.role_argv);
        arguments
            .iter()
            .chain(roles.values().flatten())
            .any(|arg| *arg == token)
    });
    DatasetIdentity {
        schema_version: 1,
        dataset: spec.id.clone(),
        output: spec.output.clone(),
        profile: request.profile,
        generator: generator.clone(),
        generator_spec: suite.generator.clone(),
        recipe,
        environment: environment
            .iter()
            .filter(|(key, _)| !matches!(key.as_str(), "HOME" | "XDG_CONFIG_HOME"))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
        path_records,
    }
}
fn validate_expected(
    expected: &DatasetSet,
    identities: &[DatasetIdentity],
) -> Result<(), BenchError> {
    if expected.inputs.len() != identities.len() || expected.generations.len() != identities.len() {
        return Err(shape_error(
            "replay requires exact generated dataset bindings",
        ));
    }
    for identity in identities {
        let record = expected
            .generations
            .get(&identity.dataset)
            .ok_or_else(|| shape_error("missing replay generation record"))?;
        validate_record(record)?;
        let input = expected
            .inputs
            .get(&identity.dataset)
            .ok_or_else(|| shape_error("missing replay input identity"))?;
        crate::artifact::validate_identity(&input.file)?;
        crate::process::utf8_path(&input.path)?;
        if record.identity != *identity
            || input.dataset != identity.dataset
            || input.file != record.file
        {
            return Err(shape_error(
                "replay recipe, generator or input identity mismatch",
            ));
        }
    }
    Ok(())
}
fn safe_path(root: &std::path::Path, relative: &str) -> Result<std::path::PathBuf, BenchError> {
    crate::invocation::directory(root)?;
    crate::suite::validate_path(relative)?;
    let mut path = root.to_path_buf();
    for part in relative.split('/') {
        path.push(part);
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(shape_error("dataset paths must not traverse symlinks"));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(path)
}
fn expand_output(argv: &[String], output: &std::path::Path) -> Result<Vec<String>, BenchError> {
    let output = crate::process::utf8_path(output)?;
    Ok(argv
        .iter()
        .map(|arg| {
            if arg == "@output" {
                output.into()
            } else {
                arg.strip_prefix("@@")
                    .map_or_else(|| arg.clone(), |suffix| format!("@{suffix}"))
            }
        })
        .collect())
}
fn verify_input_file(
    path: &std::path::Path,
    expected: &crate::FileIdentity,
) -> Result<(), BenchError> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file() {
        return Err(shape_error("dataset input is not a regular file"));
    }
    if metadata.len() != expected.bytes {
        return Err(shape_error(
            "dataset size mismatch before hash verification",
        ));
    }
    crate::verify_file(path, expected)
}
fn load_generation(
    directory: &std::path::Path,
    identity: &DatasetIdentity,
) -> Result<(crate::InputRecord, GenerationRecord), BenchError> {
    crate::invocation::directory(directory)?;
    let record_path = safe_path(directory, "generation.json")?;
    crate::fingerprint(&record_path)?;
    let record: GenerationRecord = serde_json::from_slice(&std::fs::read(record_path)?)?;
    validate_record(&record)?;
    if record.identity != *identity {
        return Err(shape_error("cached generation recipe mismatch"));
    }
    let input = input_record(directory, &record)?;
    verify_input_file(&input.path, &record.file)?;
    check_shapes(&input.path, identity)?;
    crate::verify_file(&safe_path(directory, "stdout")?, &record.stdout)?;
    crate::verify_file(&safe_path(directory, "stderr")?, &record.stderr)?;
    Ok((input, record))
}
fn input_record(
    directory: &std::path::Path,
    record: &GenerationRecord,
) -> Result<crate::InputRecord, BenchError> {
    let path = safe_path(directory, &format!("data/{}", record.identity.output))?;
    Ok(crate::InputRecord {
        dataset: record.identity.dataset.clone(),
        path,
        file: record.file.clone(),
    })
}
fn record_failure(
    directory: &std::path::Path,
    identity: &DatasetIdentity,
    expected: Option<&crate::FileIdentity>,
    output: Option<&std::path::Path>,
    error: &BenchError,
) -> Result<(), BenchError> {
    let observed_bytes = output
        .and_then(|path| std::fs::symlink_metadata(path).ok())
        .filter(std::fs::Metadata::is_file)
        .map(|metadata| metadata.len());
    let declared = declared_bytes(&identity.recipe.checks)?;
    // Never scan arbitrarily oversized failed output just to enrich diagnostics.
    let observed = if observed_bytes.is_some_and(|bytes| bytes <= declared) {
        output.and_then(|path| crate::fingerprint(path).ok())
    } else {
        None
    };
    crate::store::atomic_json(
        &directory.join("failure.json"),
        &serde_json::json!({
            "identity": identity, "expected": expected, "observed": observed, "observed_bytes": observed_bytes, "error": error.to_string(),
        }),
    )
}
fn cached_generation(
    identity: &DatasetIdentity,
    expected: Option<&GenerationRecord>,
    store: &crate::Store,
    destination: &std::path::Path,
) -> Result<Option<(crate::InputRecord, GenerationRecord)>, BenchError> {
    let _lock = store.transaction_lock()?;
    match destination.symlink_metadata() {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    }
    let loaded = load_generation(destination, identity).and_then(|value| {
        if expected.is_some_and(|old| old.file != value.1.file) {
            return Err(shape_error("cached replay dataset hash mismatch"));
        }
        Ok(value)
    });
    match loaded {
        Ok(value) => Ok(Some(value)),
        Err(error) => {
            let failure = crate::store::unique_directory(&store.root().join("datasets"), "failed")?;
            let cached: Option<GenerationRecord> = safe_path(destination, "generation.json")
                .ok()
                .and_then(|path| std::fs::read(path).ok())
                .and_then(|bytes| serde_json::from_slice(&bytes).ok());
            let original = expected.or(cached.as_ref()).map(|record| &record.file);
            let output = safe_path(destination, &format!("data/{}", identity.output)).ok();
            record_failure(&failure, identity, original, output.as_deref(), &error)?;
            Err(BenchError::Evidence(format!(
                "{error}; dataset diagnostics: {}",
                failure.display()
            )))
        }
    }
}
fn publish_generation(
    store: &crate::Store,
    staging: &std::path::Path,
    destination: &std::path::Path,
    record: GenerationRecord,
) -> Result<(crate::InputRecord, GenerationRecord), BenchError> {
    let _lock = store.transaction_lock()?;
    if destination.symlink_metadata().is_ok() {
        let existing = load_generation(destination, &record.identity)?;
        if existing.1.file != record.file {
            return Err(shape_error("concurrent generation output drift"));
        }
        return Ok(existing);
    }
    std::fs::rename(staging, destination)?;
    std::fs::File::open(store.root().join("datasets"))?.sync_all()?;
    Ok((input_record(destination, &record)?, record))
}
fn prepare_one(
    identity: &DatasetIdentity,
    expected: Option<&GenerationRecord>,
    generator: &crate::BoundExecutable,
    environment: &std::collections::BTreeMap<String, String>,
    store: &crate::Store,
    runner: &crate::ProcessRunner,
    limit: u64,
) -> Result<(crate::InputRecord, GenerationRecord), BenchError> {
    let hash = recipe_hash(identity)?;
    let parent = store.root().join("datasets");
    let destination = safe_path(&parent, &hash)?;
    if let Some(cached) = cached_generation(identity, expected, store, &destination)? {
        return Ok(cached);
    }
    let staging = crate::store::unique_directory(&parent, "pending")?;
    let data = staging.join("data");
    std::fs::create_dir(&data)?;
    let output = safe_path(&data, &identity.output)?;
    std::fs::create_dir_all(
        output
            .parent()
            .ok_or_else(|| shape_error("missing dataset output parent"))?,
    )?;
    let result = (|| {
        let argv = expand_output(&identity.recipe.argv, &output)?;
        crate::store::atomic_json(
            &staging.join("request.json"),
            &serde_json::json!({"identity":identity,"argv":argv,"max_bytes":limit}),
        )?;
        let command = crate::CommandSpec {
            program: generator.path.clone(),
            argv: argv.clone(),
            cwd: data,
            environment: environment.clone(),
            stdin: crate::CommandInput::Null,
            stdout: crate::CommandOutput::Capture,
        };
        let captures = crate::CapturePaths {
            stdout: staging.join("stdout"),
            stderr: staging.join("stderr"),
        };
        let outcome = runner.execute_with_file_limit(
            &command,
            &captures,
            &crate::OutputFileLimit {
                path: output.clone(),
                max_bytes: limit,
            },
        )?;
        crate::store::atomic_json(
            &staging.join("outcome.json"),
            &serde_json::json!({"status":format!("{:?}",outcome.status),"stopped":format!("{:?}",outcome.stopped),"stdout_bytes":outcome.stdout_bytes,"stderr_bytes":outcome.stderr_bytes}),
        )?;
        outcome.check_expected(0)?;
        crate::verify_file(&generator.path, &generator.artifact.file)?;
        store.verify_artifact(&generator.artifact)?;
        safe_path(&staging, &format!("data/{}", identity.output))?;
        let file = crate::fingerprint(&output)?;
        if file.bytes > limit {
            return Err(shape_error("generated file budget exceeded"));
        }
        if expected.is_some_and(|old| old.file != file) {
            return Err(shape_error("regenerated dataset hash mismatch"));
        }
        let shape_bytes = check_shapes(&output, identity)?;
        let record = GenerationRecord {
            identity: identity.clone(),
            recipe_hash: hash,
            file,
            shape_bytes,
            argv,
            stdout: crate::fingerprint(&captures.stdout)?,
            stderr: crate::fingerprint(&captures.stderr)?,
        };
        validate_record(&record)?;
        std::fs::File::open(&output)?.sync_all()?;
        crate::store::atomic_json(&staging.join("generation.json"), &record)?;
        std::fs::File::open(&staging)?.sync_all()?;
        publish_generation(store, &staging, &destination, record)
    })();
    match result {
        Ok(value) => Ok(value),
        Err(error) => {
            let evidence = if staging.is_dir() {
                staging
            } else {
                crate::store::unique_directory(&parent, "failed")?
            };
            let observed = safe_path(&evidence, &format!("data/{}", identity.output)).ok();
            record_failure(
                &evidence,
                identity,
                expected.map(|r| &r.file),
                observed.as_deref(),
                &error,
            )?;
            Err(BenchError::Evidence(format!(
                "{error}; dataset diagnostics: {}",
                evidence.display()
            )))
        }
    }
}

/// Verify every input and any retained generation identity without modifying expectations.
///
/// # Errors
/// Returns errors for missing/changed files, malformed provenance or failed shape checks.
/// Literal fixture inputs may omit generation records; no provenance is invented.
pub fn verify_datasets(datasets: &DatasetSet) -> Result<(), BenchError> {
    for (id, input) in &datasets.inputs {
        if input.dataset != *id {
            return Err(shape_error("dataset key/identity mismatch"));
        }
        crate::suite::validate_identifier(id)?;
        crate::process::utf8_path(&input.path)?;
        verify_input_file(&input.path, &input.file)?;
        if let Some(record) = datasets.generations.get(id) {
            validate_record(record)?;
            if record.identity.dataset != *id || record.file != input.file {
                return Err(shape_error("dataset generation binding mismatch"));
            }
            check_shapes(&input.path, &record.identity)?;
        }
    }
    if datasets
        .generations
        .keys()
        .any(|id| !datasets.inputs.contains_key(id))
    {
        return Err(shape_error("generation record has no input binding"));
    }
    Ok(())
}
fn check_shapes(
    path: &std::path::Path,
    identity: &DatasetIdentity,
) -> Result<Vec<u64>, BenchError> {
    let sizes = identity
        .recipe
        .checks
        .iter()
        .map(|rule| validate_shape(std::fs::File::open(path)?, rule))
        .collect::<Result<Vec<_>, BenchError>>()?;
    if identity.path_records {
        decode_path_records(std::io::BufReader::new(std::fs::File::open(path)?))?;
    }
    Ok(sizes)
}

pub fn declared_bytes(rules: &[CorrectnessRule]) -> Result<u64, BenchError> {
    let mut sizes = rules
        .iter()
        .map(|rule| Shape::new(rule).map(|(_, bytes)| bytes));
    let expected = sizes
        .next()
        .ok_or_else(|| shape_error("dataset requires an exact shape"))??;
    for size in sizes {
        if size? != expected {
            return Err(shape_error(
                "dataset shape assertions disagree on exact byte size",
            ));
        }
    }
    Ok(expected)
}

fn validate_record(record: &GenerationRecord) -> Result<(), BenchError> {
    let identity = &record.identity;
    crate::suite::validate_strings(&serde_json::to_value(record)?)?;
    crate::suite::validate_identifier(&identity.dataset)?;
    crate::suite::validate_path(&identity.output)?;
    crate::suite::validate_generator_arguments(&identity.recipe.argv)?;
    let output_index = identity
        .recipe
        .argv
        .iter()
        .position(|arg| arg == "@output")
        .ok_or_else(|| shape_error("missing generation output argument"))?;
    let output = record
        .argv
        .get(output_index)
        .ok_or_else(|| shape_error("invalid recorded generation argv"))?;
    if expand_output(&identity.recipe.argv, std::path::Path::new(output))? != record.argv {
        return Err(shape_error("recorded generation argv differs from recipe"));
    }
    crate::suite::validate_rules(
        &identity.recipe.checks,
        &std::collections::BTreeSet::new(),
        true,
    )?;
    let generator = &identity.generator;
    if identity.schema_version != 1
        || generator.schema_version != 1
        || generator.id
            != crate::artifact::json_identity(&(1_u32, &generator.file, &generator.build))?
        || record.recipe_hash != recipe_hash(identity)?
        || identity.generator_spec.package != "biggie"
        || identity.generator_spec.binary != "biggie"
        || identity.generator_spec.revision.len() != 40
        || !identity
            .generator_spec
            .revision
            .bytes()
            .all(|b| b.is_ascii_hexdigit())
    {
        return Err(shape_error("invalid generation recipe identity"));
    }
    for file in [
        &generator.file,
        &record.file,
        &record.stdout,
        &record.stderr,
    ] {
        crate::artifact::validate_identity(file)?;
    }
    if let Some(build) = &generator.build {
        build.validate()?;
    }
    if identity
        .environment
        .keys()
        .any(|key| !matches!(key.as_str(), "LC_ALL" | "TZ" | "CLIS_LOG_LEVEL"))
    {
        return Err(shape_error("invalid generation environment"));
    }
    let sizes = identity
        .recipe
        .checks
        .iter()
        .map(|rule| Shape::new(rule).map(|(_, bytes)| bytes))
        .collect::<Result<Vec<_>, _>>()?;
    if record.shape_bytes != sizes || sizes.iter().any(|bytes| *bytes != record.file.bytes) {
        return Err(shape_error("invalid generation shape evidence"));
    }
    Ok(())
}
fn recipe_hash(identity: &DatasetIdentity) -> Result<String, BenchError> {
    crate::artifact::json_identity(identity)
}

use crate::{BenchError, CorrectnessRule};
use std::io::{BufRead, Read};

fn shape_error(message: &str) -> BenchError {
    BenchError::Evidence(message.into())
}
fn checked(value: Option<u64>) -> Result<u64, BenchError> {
    value.ok_or_else(|| shape_error("dataset shape arithmetic overflow"))
}
fn hex_bytes(hex: &str) -> Result<Vec<u8>, BenchError> {
    if hex.is_empty() || !hex.len().is_multiple_of(2) || !hex.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(shape_error("invalid byte pattern"));
    }
    hex.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| {
            // Parse only validated ASCII pairs; no Unicode byte slicing.
            let text = std::str::from_utf8(pair).map_err(|_| shape_error("invalid hex"))?;
            u8::from_str_radix(text, 16).map_err(|_| shape_error("invalid hex"))
        })
        .collect()
}

enum Shape<'a> {
    Text {
        line_bytes: u64,
        word_span: u64,
    },
    Pattern(Vec<u8>),
    Records {
        cycle_bytes: u64,
        ends: Vec<(u64, &'a [u8])>,
    },
}
impl<'a> Shape<'a> {
    fn new(rule: &'a CorrectnessRule) -> Result<(Self, u64), BenchError> {
        match rule {
            CorrectnessRule::TextShape {
                records,
                words_per_record,
                word_length,
            } => {
                if *word_length == 0 {
                    return Err(shape_error("word length must be positive"));
                }
                let word_span = u64::from(*word_length) + 1;
                let line_bytes =
                    checked(u64::from(*words_per_record).checked_mul(word_span))?.max(1);
                Ok((
                    Self::Text {
                        line_bytes,
                        word_span,
                    },
                    checked(records.checked_mul(line_bytes))?,
                ))
            }
            CorrectnessRule::BytePattern { bytes, pattern_hex } => {
                Ok((Self::Pattern(hex_bytes(pattern_hex)?), *bytes))
            }
            CorrectnessRule::Records {
                records,
                repeat,
                cycles,
            } => {
                if records.is_empty() || *repeat == 0 {
                    return Err(shape_error("invalid record schedule"));
                }
                if records.iter().any(|record| record.contains(['\n', '\0'])) {
                    return Err(shape_error("invalid literal record"));
                }
                if *cycles == 0 {
                    return Ok((
                        Self::Records {
                            cycle_bytes: 0,
                            ends: Vec::new(),
                        },
                        0,
                    ));
                }
                let mut cycle_bytes = 0_u64;
                let mut ends = Vec::new();
                for record in records {
                    let length = checked(
                        u64::try_from(record.len())
                            .ok()
                            .and_then(|n| n.checked_add(1)),
                    )?;
                    cycle_bytes =
                        checked(cycle_bytes.checked_add(checked(length.checked_mul(*repeat))?))?;
                    ends.push((cycle_bytes, record.as_bytes()));
                }
                Ok((
                    Self::Records { cycle_bytes, ends },
                    checked(cycle_bytes.checked_mul(*cycles))?,
                ))
            }
            _ => Err(shape_error("unsupported dataset shape rule")),
        }
    }
    fn accepts(&self, offset: u64, byte: u8) -> Result<bool, BenchError> {
        match self {
            Self::Text {
                line_bytes,
                word_span,
            } => {
                let column = checked(offset.checked_rem(*line_bytes))?;
                Ok(if column == checked(line_bytes.checked_sub(1))? {
                    byte == b'\n'
                } else if checked(column.checked_rem(*word_span))?
                    == checked(word_span.checked_sub(1))?
                {
                    byte == b' '
                } else {
                    byte.is_ascii_alphanumeric()
                })
            }
            Self::Pattern(pattern) => {
                let index = usize::try_from(checked(
                    offset.checked_rem(
                        u64::try_from(pattern.len())
                            .map_err(|_| shape_error("pattern length overflow"))?,
                    ),
                )?)
                .map_err(|_| shape_error("pattern offset overflow"))?;
                Ok(pattern.get(index) == Some(&byte))
            }
            Self::Records { cycle_bytes, ends } => {
                let position = checked(offset.checked_rem(*cycle_bytes))?;
                let index = ends.partition_point(|(end, _)| *end <= position);
                let (_, record) = ends
                    .get(index)
                    .ok_or_else(|| shape_error("record offset overflow"))?;
                let start = index
                    .checked_sub(1)
                    .and_then(|i| ends.get(i))
                    .map_or(0, |(end, _)| *end);
                let length = checked(
                    u64::try_from(record.len())
                        .ok()
                        .and_then(|n| n.checked_add(1)),
                )?;
                let column = usize::try_from(checked(
                    position
                        .checked_sub(start)
                        .and_then(|n| n.checked_rem(length)),
                )?)
                .map_err(|_| shape_error("record offset overflow"))?;
                Ok(record.get(column).copied().unwrap_or(b'\n') == byte)
            }
        }
    }
}
fn validate_shape(mut reader: impl Read, rule: &CorrectnessRule) -> Result<u64, BenchError> {
    let (shape, expected) = Shape::new(rule)?;
    let mut count = 0_u64;
    let mut buffer = [0_u8; 8192];
    loop {
        let length = reader.read(&mut buffer)?;
        if length == 0 {
            break;
        }
        for byte in buffer
            .get(..length)
            .ok_or_else(|| shape_error("invalid read length"))?
        {
            if count >= expected || !shape.accepts(count, *byte)? {
                return Err(shape_error("dataset shape mismatch"));
            }
            count = checked(count.checked_add(1))?;
        }
    }
    if count != expected {
        return Err(shape_error("dataset shape size mismatch"));
    }
    Ok(count)
}

pub fn decode_path_records(mut reader: impl BufRead) -> Result<Vec<String>, BenchError> {
    let mut paths = Vec::new();
    let mut bytes = Vec::new();
    loop {
        bytes.clear();
        if reader.read_until(b'\n', &mut bytes)? == 0 {
            return Ok(paths);
        }
        if bytes.pop() != Some(b'\n') {
            return Err(BenchError::invalid("path records must end in LF"));
        }
        let record = std::str::from_utf8(&bytes)
            .map_err(|_| BenchError::invalid("path records must be UTF-8"))?;
        if record.contains(['\0', '\r']) {
            return Err(BenchError::invalid(
                "path records must not contain NUL or CR",
            ));
        }
        crate::suite::validate_path(record)?;
        paths.push(record.to_owned());
    }
}

#[cfg(test)]
mod tests {
    macro_rules! require {
        ($condition:expr $(,)?) => {
            if !$condition {
                return Err(format!("line {}: {}", line!(), stringify!($condition)).into());
            }
        };
    }
    macro_rules! require_eq {
        ($actual:expr, $expected:expr $(,)?) => {{
            let actual = &$actual;
            let expected = &$expected;
            if actual != expected {
                return Err(
                    format!("line {}: actual {actual:?}, expected {expected:?}", line!()).into(),
                );
            }
        }};
    }
    macro_rules! require_ne {
        ($actual:expr, $expected:expr $(,)?) => {{
            let actual = &$actual;
            let expected = &$expected;
            if actual == expected {
                return Err(format!("line {}: unexpectedly equal {actual:?}", line!()).into());
            }
        }};
    }
    use super::*;
    type TestResult = Result<(), Box<dyn std::error::Error>>;

    fn identity() -> Result<DatasetIdentity, BenchError> {
        let suite = crate::parse_suite(include_str!("../tests/inputs/minimal-suite.toml"))?;
        let dataset = suite
            .datasets
            .first()
            .ok_or_else(|| shape_error("missing fixture"))?;
        let file = crate::FileIdentity {
            sha256: "a".repeat(64),
            bytes: 123,
        };
        let build = None;
        Ok(DatasetIdentity {
            schema_version: 1,
            dataset: dataset.id.clone(),
            output: dataset.output.clone(),
            profile: MeasurementProfile::Full,
            generator: crate::ArtifactRecord {
                schema_version: 1,
                id: crate::artifact::json_identity(&(1_u32, &file, &build))?,
                file,
                build,
            },
            generator_spec: suite.generator,
            recipe: crate::DatasetRecipe {
                argv: dataset.argv.clone(),
                checks: dataset.checks.clone(),
            },
            environment: suite.environment,
            path_records: false,
        })
    }

    #[test]
    fn zero_record_cycles_have_zero_bytes_even_with_large_repeat() -> TestResult {
        let rule = CorrectnessRule::Records {
            records: vec!["literal".into()],
            repeat: u64::MAX,
            cycles: 0,
        };
        require_eq!(declared_bytes(std::slice::from_ref(&rule))?, 0);
        require_eq!(validate_shape(&b""[..], &rule)?, 0);
        require!(validate_shape(&b"x"[..], &rule).is_err());
        Ok(())
    }

    #[test]
    fn declared_bytes_are_exact_and_all_shape_assertions_must_agree() -> TestResult {
        let text = CorrectnessRule::TextShape {
            records: 2,
            words_per_record: 1,
            word_length: 4,
        };
        let records = CorrectnessRule::Records {
            records: vec!["ABCD".into(), "EFGH".into()],
            repeat: 1,
            cycles: 1,
        };
        require_eq!(declared_bytes(&[text.clone(), records])?, 10);
        require_eq!(
            declared_bytes(&[CorrectnessRule::BytePattern {
                bytes: 11,
                pattern_hex: "00ff0a".into()
            }])?,
            11
        );
        require!(
            declared_bytes(&[
                text,
                CorrectnessRule::BytePattern {
                    bytes: 9,
                    pattern_hex: "00".into()
                }
            ])
            .is_err()
        );
        require!(declared_bytes(&[]).is_err());
        require!(
            declared_bytes(&[CorrectnessRule::Records {
                records: vec!["a".into()],
                repeat: u64::MAX,
                cycles: 1
            }])
            .is_err()
        );
        require!(
            declared_bytes(&[CorrectnessRule::TextShape {
                records: u64::MAX,
                words_per_record: 2,
                word_length: 4
            }])
            .is_err()
        );
        require_eq!(
            declared_bytes(&[CorrectnessRule::TextShape {
                records: 2,
                words_per_record: 0,
                word_length: 4
            }])?,
            2
        );
        require_eq!(
            declared_bytes(&[CorrectnessRule::Records {
                records: vec!["é".into(), String::new()],
                repeat: 2,
                cycles: 2
            }])?,
            16
        );
        Ok(())
    }

    #[test]
    fn literal_fixture_verification_preserves_expected_hash_on_drift() -> TestResult {
        let root = assert_fs::TempDir::new()?;
        let path = root.path().join("fixture");
        std::fs::write(&path, b"original literal")?;
        let input = crate::InputRecord {
            dataset: "literal".into(),
            file: crate::fingerprint(&path)?,
            path: path.clone(),
        };
        let datasets = DatasetSet {
            inputs: [("literal".into(), input.clone())].into(),
            ..DatasetSet::default()
        };
        verify_datasets(&datasets)?;
        std::fs::write(&path, b"changed literal")?;
        require!(verify_datasets(&datasets).is_err());
        require_eq!(datasets.inputs.get("literal"), Some(&input));
        require!(datasets.generations.is_empty());
        Ok(())
    }

    #[test]
    fn recipe_hash_separates_profile_seed_checks_and_generator_provenance() -> TestResult {
        let original = identity()?;
        let hash = recipe_hash(&original)?;
        let mut changed = original.clone();
        changed.profile = MeasurementProfile::Smoke;
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original.clone();
        changed.recipe.argv.push("--seed=43".into());
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original.clone();
        changed.generator.file.sha256 = "b".repeat(64);
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original.clone();
        changed.generator_spec.features.push("csv".into());
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original.clone();
        changed.generator.build = Some(crate::BuildRecord {
            source_sha: "a".repeat(40),
            lockfile: changed.generator.file.clone(),
            policy: crate::ResolvedBuildPolicy {
                compiler: "compiler".into(),
                cargo: "cargo".into(),
                target: "target".into(),
                settings: crate::BuildPolicy::default(),
                cargo_config_hashes: vec![],
                environment_hash: None,
            },
            command: vec!["cargo".into()],
            resolved_features: vec![],
            evidence: None,
        });
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original.clone();
        changed.recipe.checks.clear();
        require_ne!(recipe_hash(&changed)?, hash);
        let mut changed = original;
        changed
            .environment
            .insert("TZ".into(), "Europe/Paris".into());
        require_ne!(recipe_hash(&changed)?, hash);
        Ok(())
    }

    #[test]
    fn replay_metadata_rejects_forged_hash_shape_and_generator_identity() -> TestResult {
        let identity = identity()?;
        let empty = crate::FileIdentity {
            sha256: "b".repeat(64),
            bytes: 0,
        };
        let argv = expand_output(
            &identity.recipe.argv,
            std::path::Path::new("/tmp/original-literal-fixture"),
        )?;
        let record = GenerationRecord {
            recipe_hash: recipe_hash(&identity)?,
            identity,
            file: crate::FileIdentity {
                sha256: "c".repeat(64),
                bytes: 10,
            },
            shape_bytes: vec![10],
            argv,
            stdout: empty.clone(),
            stderr: empty,
        };
        validate_record(&record)?;
        // Synthetic metadata only: an explicitly selected known generator may override the suite pin.
        let mut overridden = record.clone();
        let generator = &mut overridden.identity.generator;
        generator.build = Some(crate::BuildRecord {
            source_sha: "a".repeat(40),
            lockfile: generator.file.clone(),
            policy: crate::ResolvedBuildPolicy {
                compiler: "fixture compiler".into(),
                cargo: "fixture cargo".into(),
                target: "fixture target".into(),
                settings: crate::BuildPolicy::default(),
                cargo_config_hashes: vec![],
                environment_hash: None,
            },
            command: vec!["fixture build".into()],
            resolved_features: vec![],
            evidence: None,
        });
        generator.id = crate::artifact::json_identity(&(1_u32, &generator.file, &generator.build))?;
        overridden.recipe_hash = recipe_hash(&overridden.identity)?;
        validate_record(&overridden)?;
        let expected = DatasetSet {
            inputs: [(
                "tiny".into(),
                crate::InputRecord {
                    dataset: "tiny".into(),
                    path: "/tmp/absent-original".into(),
                    file: overridden.file.clone(),
                },
            )]
            .into(),
            generations: [("tiny".into(), overridden.clone())].into(),
        };
        validate_expected(&expected, &[overridden.identity])?;
        require!(validate_expected(&expected, std::slice::from_ref(&record.identity)).is_err());
        let mut changed = record.clone();
        changed.recipe_hash = "f".repeat(64);
        require!(validate_record(&changed).is_err());
        let mut changed = record.clone();
        changed.shape_bytes = vec![9];
        require!(validate_record(&changed).is_err());
        let mut changed = record.clone();
        changed.identity.generator.id = "forged".into();
        require!(validate_record(&changed).is_err());
        let mut changed = record;
        changed.file.sha256 = "bad".into();
        require!(validate_record(&changed).is_err());
        Ok(())
    }

    #[test]
    fn fixed_text_checks_every_separator_and_ascii_word() -> TestResult {
        let rule = CorrectnessRule::TextShape {
            records: 2,
            words_per_record: 2,
            word_length: 2,
        };
        require_eq!(validate_shape(&b"aB 09\nCd eF\n"[..], &rule)?, 12);
        for bytes in [
            b"aB 09\nCd eF".as_slice(),
            b"aB 09\nCd eF\nX",
            b"aB 09\nCd\teF\n",
            b"aB 09\nCd e!\n",
            b"aB 09\rCd eF\n",
        ] {
            require!(validate_shape(bytes, &rule).is_err());
        }
        require_eq!(
            validate_shape(
                &b"\n\n"[..],
                &CorrectnessRule::TextShape {
                    records: 2,
                    words_per_record: 0,
                    word_length: 4
                }
            )?,
            2
        );
        require!(
            validate_shape(
                &b""[..],
                &CorrectnessRule::TextShape {
                    records: u64::MAX,
                    words_per_record: 2,
                    word_length: 4
                }
            )
            .is_err()
        );
        Ok(())
    }

    #[test]
    fn literal_schedule_checks_repeat_cycle_empty_and_unicode() -> TestResult {
        let rule = CorrectnessRule::Records {
            records: vec!["é".into(), String::new()],
            repeat: 2,
            cycles: 2,
        };
        require_eq!(
            validate_shape(
                &b"\xc3\xa9\n\xc3\xa9\n\n\n\xc3\xa9\n\xc3\xa9\n\n\n"[..],
                &rule
            )?,
            16
        );
        require!(
            validate_shape(
                &b"\xc3\xa9\n\n\xc3\xa9\n\n\xc3\xa9\n\n\xc3\xa9\n\n"[..],
                &rule
            )
            .is_err()
        );
        require!(
            validate_shape(
                &b""[..],
                &CorrectnessRule::Records {
                    records: vec!["xx".into()],
                    repeat: u64::MAX,
                    cycles: 2
                }
            )
            .is_err()
        );
        Ok(())
    }

    #[test]
    fn byte_pattern_checks_partial_final_pattern_and_buffer_boundary() -> TestResult {
        let bytes: Vec<u8> = [0, 255, 10].into_iter().cycle().take(8194).collect();
        let rule = CorrectnessRule::BytePattern {
            bytes: 8194,
            pattern_hex: "00ff0a".into(),
        };
        require_eq!(validate_shape(bytes.as_slice(), &rule)?, 8194);
        let mut bad = bytes;
        *bad.get_mut(8192).ok_or("missing boundary byte")? = 42;
        require!(validate_shape(bad.as_slice(), &rule).is_err());
        for hex in ["", "f", "zz", "é"] {
            require!(
                validate_shape(
                    &b""[..],
                    &CorrectnessRule::BytePattern {
                        bytes: 0,
                        pattern_hex: hex.into()
                    }
                )
                .is_err()
            );
        }
        Ok(())
    }

    #[test]
    fn path_records_reject_unsafe_or_malformed_bytes() -> TestResult {
        require_eq!(
            decode_path_records(&b"a/b\nc d/\xc3\xa9\n"[..])?,
            ["a/b", "c d/é"]
        );
        for bytes in [
            b"\n".as_slice(),
            b"/abs\n",
            b"../x\n",
            b"a/../b\n",
            b"a//b\n",
            b"a\r\n",
            b"a\0\n",
            b"a",
            b"a\\b\n",
            b"C:/x\n",
            b"\xff\n",
        ] {
            require!(decode_path_records(bytes).is_err());
        }
        require!(decode_path_records(&b""[..])?.is_empty());
        Ok(())
    }
}
