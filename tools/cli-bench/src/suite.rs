use crate::{
    BenchError, BuildPolicy, CaseOverride, CaseSpec, CorrectnessRule, Limits, MutationSetup,
    StdinPolicy, StdoutPolicy, Suite, Work,
};
use std::collections::BTreeSet;

/// Parse a UTF-8 suite and validate it without executing tools or doing I/O.
///
/// # Errors
/// Returns a typed failure for malformed TOML or a violated suite constraint.
pub fn parse_suite(source: &str) -> Result<Suite, BenchError> {
    let suite = toml::from_str(source)?;
    validate_suite(&suite)?;
    Ok(suite)
}

/// Validate direct Rust callers using the same semantic checks as parsing.
///
/// # Errors
/// Returns a typed configuration failure if a constraint is violated.
pub fn validate_suite(suite: &Suite) -> Result<(), BenchError> {
    if suite.schema_version != 1 {
        return Err(BenchError::UnsupportedSchema(suite.schema_version));
    }
    // This visits every string/key, including future optional model fields.
    let strings = serde_json::to_value(suite)
        .map_err(|error| BenchError::invalid(format!("cannot inspect suite strings: {error}")))?;
    validate_strings(&strings)?;
    for identifier in [&suite.id, &suite.package, &suite.binary] {
        validate_identifier(identifier)?;
    }
    validate_limits(&suite.limits)?;
    validate_build(&suite.build)?;
    require(
        suite.generator.package == "biggie" && suite.generator.binary == "biggie",
        "generator package and binary must be biggie",
    )?;
    require(
        suite.generator.revision.len() == 40
            && suite
                .generator
                .revision
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit()),
        "generator revision must be a full 40-digit hexadecimal commit",
    )?;
    validate_nonempty_strings(suite.generator.features.iter())?;
    for key in suite.environment.keys() {
        require(
            matches!(key.as_str(), "LC_ALL" | "TZ" | "CLIS_LOG_LEVEL"),
            format!("environment key {key:?} is not allowlisted"),
        )?;
    }
    require(!suite.cases.is_empty(), "suite requires at least one case")?;
    let count = u64::try_from(suite.cases.len())
        .map_err(|error| BenchError::invalid(format!("cannot represent case count: {error}")))?;
    require(
        count <= suite.limits.max_cases,
        "case count exceeds max_cases",
    )?;

    let mut dataset_ids = BTreeSet::new();
    for dataset in &suite.datasets {
        validate_identifier(&dataset.id)?;
        require(
            dataset_ids.insert(dataset.id.as_str()),
            format!("duplicate dataset ID {:?}", dataset.id),
        )?;
        validate_path(&dataset.output)?;
    }
    for dataset in &suite.datasets {
        validate_generator_arguments(&dataset.argv)?;
        validate_rules(&dataset.checks, &dataset_ids, true)?;
        for recipe in dataset.profiles.values() {
            validate_generator_arguments(&recipe.argv)?;
            validate_rules(&recipe.checks, &dataset_ids, true)?;
        }
    }
    let mut case_ids = BTreeSet::new();
    for case in &suite.cases {
        validate_identifier(&case.id)?;
        require(
            case_ids.insert(case.id.as_str()),
            format!("duplicate case ID {:?}", case.id),
        )?;
        validate_case(case, &dataset_ids)?;
    }
    Ok(())
}

fn require(condition: bool, message: impl Into<String>) -> Result<(), BenchError> {
    if condition {
        Ok(())
    } else {
        Err(BenchError::invalid(message))
    }
}

pub fn validate_strings(value: &serde_json::Value) -> Result<(), BenchError> {
    match value {
        serde_json::Value::String(value) => {
            require(!value.contains('\0'), "suite strings must not contain NUL")
        }
        serde_json::Value::Array(values) => values.iter().try_for_each(validate_strings),
        serde_json::Value::Object(fields) => fields.iter().try_for_each(|(key, value)| {
            require(!key.contains('\0'), "suite keys must not contain NUL")?;
            validate_strings(value)
        }),
        _ => Ok(()),
    }
}

pub fn validate_identifier(value: &str) -> Result<(), BenchError> {
    require(
        !value.is_empty()
            && value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_')),
        format!("invalid identifier {value:?}: use ASCII letters, digits, hyphens or underscores"),
    )
}

fn validate_nonempty_strings<'a>(
    values: impl IntoIterator<Item = &'a String>,
) -> Result<(), BenchError> {
    values
        .into_iter()
        .try_for_each(|value| require(!value.trim().is_empty(), "build settings must be nonempty"))
}

fn validate_build(build: &BuildPolicy) -> Result<(), BenchError> {
    validate_nonempty_strings(
        build
            .toolchain
            .iter()
            .chain(build.target.iter())
            .chain(build.features.iter())
            .chain(build.rustflags.iter()),
    )
}

fn validate_limits(limits: &Limits) -> Result<(), BenchError> {
    for (name, count) in [
        ("max_cases", limits.max_cases),
        ("max_stream_bytes", limits.max_stream_bytes),
        ("max_generated_bytes", limits.max_generated_bytes),
        ("max_generated_file_bytes", limits.max_generated_file_bytes),
        ("max_evidence_bytes", limits.max_evidence_bytes),
        ("sample_timeout_seconds", limits.sample_timeout_seconds),
        ("build_timeout_seconds", limits.build_timeout_seconds),
    ] {
        require(count > 0, format!("{name} must be positive"))?;
    }
    let defaults = Limits::default();
    if limits.sample_timeout_seconds != defaults.sample_timeout_seconds
        || limits.build_timeout_seconds != defaults.build_timeout_seconds
    {
        require(
            limits
                .timeout_reason
                .as_ref()
                .is_some_and(|reason| !reason.trim().is_empty()),
            "deadline overrides require a nonempty timeout_reason",
        )?;
    }
    Ok(())
}

pub fn validate_path(path: &str) -> Result<(), BenchError> {
    let drive_prefix = path.as_bytes().get(1) == Some(&b':')
        && path.as_bytes().first().is_some_and(u8::is_ascii_alphabetic);
    require(
        !path.is_empty()
            && !path.contains('\\')
            && !drive_prefix
            && path
                .split('/')
                .all(|component| !matches!(component, "" | "." | "..")),
        format!("invalid contained relative path {path:?}"),
    )
}

fn validate_paths(paths: &[String]) -> Result<(), BenchError> {
    let mut unique = BTreeSet::new();
    for path in paths {
        validate_path(path)?;
        require(
            unique.insert(path),
            format!("duplicate directory path {path:?}"),
        )?;
    }
    Ok(())
}

fn validate_reference(id: &str, datasets: &BTreeSet<&str>) -> Result<(), BenchError> {
    require(datasets.contains(id), format!("unknown dataset ID {id:?}"))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArgumentToken<'a> {
    Literal,
    Input(&'a str),
    Scratch(&'a str),
    Records(&'a str),
    Output,
}

pub fn parse_argument(argument: &str) -> Result<ArgumentToken<'_>, BenchError> {
    if argument.starts_with("@@") {
        return Ok(ArgumentToken::Literal);
    }
    if argument == "@output" {
        return Ok(ArgumentToken::Output);
    }
    if let Some(id) = argument.strip_prefix("@input:") {
        validate_identifier(id)?;
        return Ok(ArgumentToken::Input(id));
    }
    if let Some(id) = argument.strip_prefix("@records:") {
        validate_identifier(id)?;
        return Ok(ArgumentToken::Records(id));
    }
    if let Some(path) = argument.strip_prefix("@scratch:") {
        validate_path(path)?;
        return Ok(ArgumentToken::Scratch(path));
    }
    require(
        !argument.starts_with('@'),
        format!("unknown whole-argument token {argument:?}"),
    )?;
    Ok(ArgumentToken::Literal)
}

fn validate_arguments(
    arguments: &[String],
    datasets: &BTreeSet<&str>,
    generator: bool,
) -> Result<(), BenchError> {
    let mut outputs = 0_usize;
    for argument in arguments {
        match parse_argument(argument)? {
            ArgumentToken::Literal => {}
            ArgumentToken::Output if generator => outputs = outputs.saturating_add(1),
            ArgumentToken::Output => {
                return Err(BenchError::invalid(
                    "@output is only allowed in dataset generator argv",
                ));
            }
            ArgumentToken::Input(id) | ArgumentToken::Records(id) if !generator => {
                validate_reference(id, datasets)?;
            }
            ArgumentToken::Scratch(_) if !generator => {}
            _ => {
                return Err(BenchError::invalid(
                    "dataset argv permits only @output and escaped literal tokens",
                ));
            }
        }
    }
    if generator {
        require(
            outputs == 1,
            "dataset argv requires exactly one @output token",
        )?;
    }
    Ok(())
}

fn generator_values<'a>(
    argv: &'a [String],
    long: &str,
    short: &str,
) -> Result<Vec<&'a str>, BenchError> {
    let mut values = Vec::new();
    let mut arguments = argv.iter().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == "--" {
            break;
        }
        if argument == long || argument == short {
            values.push(
                arguments
                    .next()
                    .ok_or_else(|| BenchError::invalid(format!("{argument} requires a value")))?
                    .as_str(),
            );
        } else if let Some(value) = argument
            .strip_prefix(long)
            .and_then(|suffix| suffix.strip_prefix('='))
        {
            values.push(value);
        }
    }
    Ok(values)
}

fn valid_hex(hex: &str, empty_allowed: bool) -> bool {
    (empty_allowed || !hex.is_empty())
        && hex.len().is_multiple_of(2)
        && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn has_literal_records(argv: &[String]) -> Result<bool, BenchError> {
    let mut has_records = false;
    let mut arguments = argv.iter().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == "--" {
            break;
        }
        if argument == "--record" || argument == "-r" {
            // A consumed record is literal data, even when it resembles a file option.
            arguments
                .next()
                .ok_or_else(|| BenchError::invalid(format!("{argument} requires a value")))?;
            has_records = true;
        } else if argument.starts_with("--record=") {
            has_records = true;
        } else {
            require(
                !argument.starts_with("-f")
                    && argument != "--records-file"
                    && !argument.starts_with("--records-file="),
                "external record source files are unsupported",
            )?;
        }
    }
    Ok(has_records)
}

pub fn validate_generator_arguments(argv: &[String]) -> Result<(), BenchError> {
    validate_arguments(argv, &BTreeSet::new(), true)?;
    let command = argv
        .first()
        .map(String::as_str)
        .ok_or_else(|| BenchError::invalid("empty generator argv"))?;
    require(
        matches!(command, "text" | "bytes" | "records" | "fields"),
        "dataset generator requires text, bytes, records or fields",
    )?;
    let seeds = generator_values(argv, "--seed", "-s")?;
    for seed in &seeds {
        require(
            seed.parse::<u64>().is_ok(),
            "Biggie seed must be an explicit u64",
        )?;
    }
    let deterministic = match command {
        "bytes" => {
            let patterns = generator_values(argv, "--pattern-hex", "-p")?;
            for pattern in &patterns {
                require(
                    valid_hex(pattern, false),
                    "Biggie pattern must be nonempty even-length hex",
                )?;
            }
            !seeds.is_empty() || !patterns.is_empty()
        }
        "records" => has_literal_records(argv)?,
        "fields" => !seeds.is_empty() || !generator_values(argv, "--field-value", "-v")?.is_empty(),
        _ => !seeds.is_empty(),
    };
    require(
        deterministic,
        "Biggie argv requires an explicit seed or literal pattern/schedule",
    )
}

pub fn validate_case(case: &CaseSpec, datasets: &BTreeSet<&str>) -> Result<(), BenchError> {
    require(
        !case.purpose.trim().is_empty(),
        "case purpose must be nonempty",
    )?;
    require(
        (0..=255).contains(&case.expected_status),
        "expected_status must be 0 through 255",
    )?;
    match &case.io.stdin {
        StdinPolicy::Null {} => {}
        StdinPolicy::RegularFile { dataset } => validate_reference(dataset, datasets)?,
        StdinPolicy::Pipe { dataset } => {
            validate_reference(dataset, datasets)?;
            require(
                case.expected_status == 0,
                "pipe cases require expected_status zero",
            )?;
        }
    }
    if let StdoutPolicy::ScratchFile { path } = &case.io.stdout {
        validate_path(path)?;
    }
    if let MutationSetup::Directories { paths } = &case.mutation {
        validate_paths(paths)?;
    }
    validate_case_profile(case, None, datasets)?;
    for profile in case.profiles.values() {
        validate_case_profile(case, Some(profile), datasets)?;
    }
    Ok(())
}

fn validate_case_profile(
    case: &CaseSpec,
    profile: Option<&CaseOverride>,
    datasets: &BTreeSet<&str>,
) -> Result<(), BenchError> {
    let argv = profile
        .and_then(|overrides| overrides.argv.as_ref())
        .unwrap_or(&case.argv);
    let role_argv = profile
        .and_then(|overrides| overrides.role_argv.as_ref())
        .unwrap_or(&case.role_argv);
    let correctness = profile
        .and_then(|overrides| overrides.correctness.as_ref())
        .unwrap_or(&case.correctness);
    let work = profile
        .and_then(|overrides| overrides.work.as_ref())
        .or(case.work.as_ref());
    validate_arguments(argv, datasets, false)?;
    for arguments in role_argv.values() {
        validate_arguments(arguments, datasets, false)?;
    }
    validate_rules(correctness, datasets, false)?;
    if let Some(Work { amount, .. }) = work {
        require(*amount > 0, "work amount must be positive")?;
    }
    Ok(())
}

pub fn validate_rules(
    rules: &[CorrectnessRule],
    datasets: &BTreeSet<&str>,
    dataset: bool,
) -> Result<(), BenchError> {
    require(!rules.is_empty(), "correctness checks must be nonempty")?;
    for rule in rules {
        require(
            !dataset
                || matches!(
                    rule,
                    CorrectnessRule::BytePattern { .. }
                        | CorrectnessRule::TextShape { .. }
                        | CorrectnessRule::Records { .. }
                ),
            "dataset checks require byte-pattern, text-shape or records",
        )?;
        match rule {
            CorrectnessRule::Comparator { .. } => require(
                !dataset,
                "dataset checks must be independent of executable comparators",
            )?,
            CorrectnessRule::Literal { .. } | CorrectnessRule::EmptyStderr {} => {}
            CorrectnessRule::Hex { hex, .. } => require(
                valid_hex(hex, true),
                "expected hex bytes must be even-length hexadecimal",
            )?,
            CorrectnessRule::BytePattern { pattern_hex, .. } => require(
                valid_hex(pattern_hex, false),
                "expected byte pattern must be nonempty even-length hex",
            )?,
            CorrectnessRule::TextShape { word_length, .. } => {
                require(*word_length > 0, "text word_length must be positive")?;
            }
            CorrectnessRule::Records {
                records, repeat, ..
            } => {
                require(
                    !records.is_empty() && *repeat > 0,
                    "record schedule must be nonempty with positive repeat",
                )?;
                require(
                    records.iter().all(|record| !record.contains('\n')),
                    "literal records must not contain LF",
                )?;
            }
            CorrectnessRule::TailSlice { dataset, .. } => validate_reference(dataset, datasets)?,
            CorrectnessRule::DirectoryTree { paths, .. } => validate_paths(paths)?,
        }
    }
    if dataset {
        crate::dataset::declared_bytes(rules)
            .map_err(|error| BenchError::invalid(error.to_string()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{parse_suite, validate_suite};
    use crate::{
        CaseOverride, CorrectnessRule, DatasetRecipe, ErrorKind, MeasurementProfile, MutationSetup,
        StdinPolicy, StdoutPolicy, Stream, Work, WorkUnit,
    };

    const MINIMAL: &str = include_str!("../tests/inputs/minimal-suite.toml");
    type TestResult = Result<(), Box<dyn std::error::Error>>;

    fn check_equal<T: Copy + std::fmt::Debug + PartialEq>(actual: T, expected: T) -> TestResult {
        if actual == expected {
            Ok(())
        } else {
            Err(format!("expected {expected:?}, got {actual:?}").into())
        }
    }

    fn rejects(source: &str) {
        assert_eq!(
            parse_suite(source).err().map(|error| error.kind()),
            Some(ErrorKind::InvalidSuite)
        );
    }

    #[test]
    fn accepts_the_checked_in_minimal_suite() {
        assert!(parse_suite(include_str!("../tests/inputs/minimal-suite.toml")).is_ok());
    }

    #[test]
    fn rejects_unsupported_schema() {
        let invalid = include_str!("../tests/inputs/minimal-suite.toml")
            .replace("schema_version = 1", "schema_version = 999");
        assert_eq!(
            parse_suite(&invalid).err().map(|e| e.kind()),
            Some(ErrorKind::UnsupportedSchema)
        );
    }

    #[test]
    fn rejects_unknown_fields_at_every_nested_boundary() {
        for (needle, replacement) in [
            ("schema_version = 1", "schema_version = 1\nextra = true"),
            ("[generator]", "[generator]\nextra = true"),
            ("[[datasets]]", "[[datasets]]\nextra = true"),
            ("[[cases]]", "[[cases]]\nextra = true"),
            (
                "[cases.io.stdin]",
                "[cases.io]\nextra = true\n[cases.io.stdin]",
            ),
            ("kind = \"null\"", "kind = \"null\"\nextra = true"),
            (
                "kind = \"drained-pipe\"",
                "kind = \"drained-pipe\"\nextra = true",
            ),
            (
                "kind = \"drained-pipe\"",
                "kind = \"discard\"\nextra = true",
            ),
            (
                "kind = \"empty-stderr\"",
                "kind = \"empty-stderr\", extra = true",
            ),
            ("[environment]", "[limits]\nextra = true\n[environment]"),
            ("[environment]", "[build]\nextra = true\n[environment]"),
            (
                "[cases.io.stdin]",
                "[cases.profiles.smoke]\nextra = true\n[cases.io.stdin]",
            ),
            (
                "[[cases]]",
                "[datasets.profiles.smoke]\nargv = [\"text\", \"--seed\", \"42\", \"@output\"]\nchecks = [{kind = \"text-shape\", records = 2, words_per_record = 1, word_length = 4}]\nextra = true\n[[cases]]",
            ),
            (
                "expected_status = 0",
                "expected_status = 0\nwork = {amount = 1, unit = \"bytes\", extra = true}",
            ),
            (
                "expected_status = 0",
                "expected_status = 0\nmutation = {kind = \"directories\", paths = [], extra = true}",
            ),
            (
                "expected_status = 0",
                "expected_status = 0\nmutation = {kind = \"none\", extra = true}",
            ),
        ] {
            let invalid = MINIMAL.replace(needle, replacement);
            assert_ne!(invalid, MINIMAL);
            assert_eq!(
                parse_suite(&invalid).err().map(|error| error.kind()),
                Some(ErrorKind::InvalidSuite),
                "unknown-field boundary: {replacement}"
            );
        }
    }

    #[test]
    fn rejects_duplicate_dataset_ids() {
        rejects(&MINIMAL.replace("[[cases]]", "[[datasets]]\nid = \"tiny\"\nargv = [\"text\", \"--seed\", \"42\", \"@output\"]\noutput = \"other.txt\"\nchecks = [{kind = \"text-shape\", records = 2, words_per_record = 1, word_length = 4}]\n[[cases]]"));
    }

    #[test]
    fn rejects_malformed_toml_and_unknown_closed_variants() {
        rejects("schema_version = [");
        for (needle, replacement) in [
            ("kind = \"null\"", "kind = \"network\""),
            ("selected-baselines", "all-tools"),
            ("unit = \"lines\"", "unit = \"graphemes\""),
            ("stream = \"stdout\"", "stream = \"merged\""),
            (
                "expected_status = 0",
                "expected_status = 0\nrole_argv = {other = []}",
            ),
            (
                "[cases.io.stdin]",
                "[cases.profiles.custom]\nargv = []\n[cases.io.stdin]",
            ),
        ] {
            rejects(&MINIMAL.replace(needle, replacement));
        }
    }

    #[test]
    fn accepts_complete_profile_recipes_and_contained_role_overrides() -> TestResult {
        let source = MINIMAL.replace("[[cases]]", "[datasets.profiles.smoke]\nargv = [\"records\", \"-r\", \"small\", \"@output\"]\nchecks = [{kind = \"records\", records = [\"small\"], repeat = 1, cycles = 1}]\n[[cases]]")
            .replace("expected_status = 0", "expected_status = 0\nrole_argv = {previous = [\"@records:tiny\", \"@scratch:nested/path\"]}\nmutation = {kind = \"directories\", paths = []}\nwork = {amount = 2, unit = \"records\"}")
            .replace("[cases.io.stdin]", "[cases.profiles.smoke]\nargv = [\"@@output\"]\nrole_argv = {reference = []}\ncorrectness = [{kind = \"hex\", stream = \"stdout\", hex = \"\"}]\nwork = {amount = 1, unit = \"records\"}\n[cases.io.stdin]");
        let suite = parse_suite(&source)?;
        check_equal(
            suite
                .datasets
                .first()
                .ok_or("fixture dataset absent")?
                .profiles
                .len(),
            1,
        )?;
        check_equal(
            suite
                .cases
                .first()
                .ok_or("fixture case absent")?
                .profiles
                .len(),
            1,
        )?;
        validate_suite(&suite)?;
        Ok(())
    }

    #[test]
    fn rejects_duplicate_case_ids() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .cases
            .push(suite.cases.first().ok_or("fixture case absent")?.clone());
        check_equal(
            validate_suite(&suite).err().map(|error| error.kind()),
            Some(ErrorKind::InvalidSuite),
        )?;
        Ok(())
    }

    #[test]
    fn rejects_zero_resource_limits() {
        for field in [
            "max_cases",
            "max_stream_bytes",
            "max_generated_bytes",
            "max_generated_file_bytes",
            "max_evidence_bytes",
            "sample_timeout_seconds",
            "build_timeout_seconds",
        ] {
            rejects(&MINIMAL.replace(
                "[environment]",
                &format!("[limits]\n{field} = 0\n[environment]"),
            ));
        }
    }

    #[test]
    fn rejects_case_counts_over_the_explicit_limit() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite.limits.max_cases = 1;
        let mut second = suite.cases.first().ok_or("fixture case absent")?.clone();
        second.id = "second".into();
        suite.cases.push(second);
        check_equal(
            validate_suite(&suite).err().map(|error| error.kind()),
            Some(ErrorKind::InvalidSuite),
        )?;
        Ok(())
    }

    #[test]
    fn rejects_empty_case_sets() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite.cases.clear();
        check_equal(
            validate_suite(&suite).err().map(|error| error.kind()),
            Some(ErrorKind::InvalidSuite),
        )?;
        Ok(())
    }

    #[test]
    fn requires_a_reason_for_changed_deadlines() {
        rejects(&MINIMAL.replace(
            "[environment]",
            "[limits]\nsample_timeout_seconds = 121\n[environment]",
        ));
        rejects(&MINIMAL.replace(
            "[environment]",
            "[limits]\nbuild_timeout_seconds = 1801\ntimeout_reason = \" \"\n[environment]",
        ));
        assert!(parse_suite(&MINIMAL.replace("[environment]", "[limits]\nsample_timeout_seconds = 121\ntimeout_reason = \"Slow explicit workload\"\n[environment]")).is_ok());
    }

    #[test]
    fn rejects_unknown_tokens_and_dataset_references() {
        for token in [
            "@unknown",
            "@input:",
            "@records:",
            "@scratch:",
            "@output",
            "@input:absent",
            "@records:absent",
        ] {
            rejects(&MINIMAL.replace("@input:tiny", token));
        }
    }

    #[test]
    fn rejects_escaping_scratch_and_output_paths() {
        for path in [
            "",
            "..",
            "../outside",
            "/absolute",
            "a/../b",
            "a/./b",
            "a//b",
            "a/",
            "C:outside",
            "a\\b",
        ] {
            rejects(&MINIMAL.replace("tiny.txt", &path.replace('\\', "\\\\")));
            rejects(&MINIMAL.replace(
                "@input:tiny",
                &format!("@scratch:{}", path.replace('\\', "\\\\")),
            ));
        }
    }

    #[test]
    fn preserves_escaped_and_embedded_at_signs() -> TestResult {
        let suite = parse_suite(&MINIMAL.replace("@input:tiny", "@@input:absent"))?;
        check_equal(
            suite
                .cases
                .first()
                .ok_or("fixture case absent")?
                .argv
                .last()
                .map(String::as_str),
            Some("@@input:absent"),
        )?;
        check_equal(
            parse_suite(&MINIMAL.replace("@input:tiny", "--file=@input:absent")).is_ok(),
            true,
        )?;
        Ok(())
    }

    #[test]
    fn rejects_nuls_in_strings_across_the_model() {
        for needle in [
            "tailr-minimal",
            "tiny.txt",
            "Read the final line",
            "@input:tiny",
            "UTC",
        ] {
            rejects(&MINIMAL.replace(needle, "\\u0000"));
        }
    }

    #[test]
    fn rejects_missing_or_invalid_biggie_seed() {
        for seed in ["", "-1", "18446744073709551616", "not-a-seed"] {
            rejects(&MINIMAL.replace("\"--seed\", \"42\", ", &format!("\"--seed\", \"{seed}\", ")));
        }
        rejects(&MINIMAL.replace("\"--seed\", \"42\", ", ""));
        rejects(&MINIMAL.replace("\"--seed\", \"42\", ", "\"--\", \"--seed\", \"42\", "));
    }

    #[test]
    fn requires_exactly_one_generator_output_token() {
        rejects(&MINIMAL.replace("\"@output\"", "\"@@output\""));
        rejects(&MINIMAL.replace("\"@output\"", "\"@output\", \"@output\""));
        rejects(&MINIMAL.replace("\"@output\"", "\"@input:tiny\""));
    }

    #[test]
    fn validates_byte_patterns_and_literal_record_schedules() {
        let text = "\"text\", \"--lines\", \"2\", \"--words-per-line\", \"1\", \"--word-length\", \"4\", \"--seed\", \"42\", \"@output\"";
        for argv in [
            "\"bytes\", \"--bytes\", \"3\", \"@output\"",
            "\"bytes\", \"-p\", \"0\", \"@output\"",
            "\"bytes\", \"-p\", \"zz\", \"@output\"",
            "\"records\", \"@output\"",
            "\"records\", \"--records-file\", \"corpus.txt\", \"-r\", \"literal\", \"@output\"",
            "\"pair\", \"--seed\", \"42\", \"@output\"",
        ] {
            rejects(&MINIMAL.replace(text, argv));
        }
        for argv in [
            "\"bytes\", \"--bytes\", \"3\", \"--pattern-hex=00ff\", \"@output\"",
            "\"records\", \"-r\", \"\", \"--record=hello\", \"@output\"",
            "\"fields\", \"--field-value=hello\", \"@output\"",
            "\"text\", \"--seed=42\", \"@output\"",
        ] {
            assert!(parse_suite(&MINIMAL.replace(text, argv)).is_ok());
        }
    }

    #[test]
    fn rejects_attached_external_record_sources_when_parsing() {
        let text = "\"text\", \"--lines\", \"2\", \"--words-per-line\", \"1\", \"--word-length\", \"4\", \"--seed\", \"42\", \"@output\"";
        for source in ["-fcorpus.txt", "-f=corpus.txt"] {
            rejects(&MINIMAL.replace(
                text,
                &format!("\"records\", \"-r\", \"literal\", \"{source}\", \"@output\""),
            ));
        }
    }

    #[test]
    fn rejects_attached_external_record_sources_for_direct_recipes() -> TestResult {
        for source in ["-fcorpus.txt", "-f=corpus.txt"] {
            let argv = vec![
                "records".into(),
                source.into(),
                "-r".into(),
                "literal".into(),
                "@output".into(),
            ];
            let mut suite = parse_suite(MINIMAL)?;
            suite
                .datasets
                .first_mut()
                .ok_or("fixture dataset absent")?
                .argv = argv;
            check_equal(
                validate_suite(&suite).err().map(|error| error.kind()),
                Some(ErrorKind::InvalidSuite),
            )?;
        }
        Ok(())
    }

    #[test]
    fn rejects_attached_external_record_sources_for_profile_recipes() -> TestResult {
        for source in ["-fcorpus.txt", "-f=corpus.txt"] {
            for profile in [MeasurementProfile::Full, MeasurementProfile::Smoke] {
                let mut suite = parse_suite(MINIMAL)?;
                suite
                    .datasets
                    .first_mut()
                    .ok_or("fixture dataset absent")?
                    .profiles
                    .insert(
                        profile,
                        DatasetRecipe {
                            argv: vec![
                                "records".into(),
                                source.into(),
                                "-r".into(),
                                "literal".into(),
                                "@output".into(),
                            ],
                            checks: vec![CorrectnessRule::Records {
                                records: vec!["literal".into()],
                                repeat: 1,
                                cycles: 1,
                            }],
                        },
                    );
                check_equal(
                    validate_suite(&suite).err().map(|error| error.kind()),
                    Some(ErrorKind::InvalidSuite),
                )?;
            }
        }
        Ok(())
    }

    #[test]
    fn preserves_external_source_spellings_used_as_literal_records() -> TestResult {
        for literal in [
            "-f",
            "-fcorpus.txt",
            "-f=corpus.txt",
            "--records-file",
            "--records-file=corpus.txt",
        ] {
            for record_option in ["-r", "--record"] {
                let mut suite = parse_suite(MINIMAL)?;
                suite
                    .datasets
                    .first_mut()
                    .ok_or("fixture dataset absent")?
                    .argv = vec![
                    "records".into(),
                    record_option.into(),
                    literal.into(),
                    "@output".into(),
                ];
                validate_suite(&suite)?;
            }
            let mut suite = parse_suite(MINIMAL)?;
            suite
                .datasets
                .first_mut()
                .ok_or("fixture dataset absent")?
                .argv = vec![
                "records".into(),
                format!("--record={literal}"),
                "@output".into(),
            ];
            validate_suite(&suite)?;
        }
        Ok(())
    }

    #[test]
    fn rejects_invalid_generator_identity() {
        rejects(&MINIMAL.replace("0d8caa8387d446e91ef263c1ecab88870b735eb5", "HEAD"));
        rejects(&MINIMAL.replace("package = \"biggie\"", "package = \"tailr\""));
        rejects(&MINIMAL.replace("binary = \"biggie\"", "binary = \"other\""));
    }

    #[test]
    fn merges_partial_child_environment_over_safe_defaults() -> TestResult {
        let suite = parse_suite(
            &MINIMAL
                .replace("LC_ALL = \"C\"\n", "")
                .replace("TZ = \"UTC\"", "TZ = \"Europe/Paris\"")
                .replace("CLIS_LOG_LEVEL = \"off\"\n", ""),
        )?;
        check_equal(
            suite.environment.get("LC_ALL").map(String::as_str),
            Some("C"),
        )?;
        check_equal(
            suite.environment.get("TZ").map(String::as_str),
            Some("Europe/Paris"),
        )?;
        check_equal(
            suite.environment.get("CLIS_LOG_LEVEL").map(String::as_str),
            Some("off"),
        )?;
        Ok(())
    }

    #[test]
    fn rejects_non_allowlisted_environment_keys() {
        rejects(&MINIMAL.replace(
            "[environment]",
            "[environment]\nLD_PRELOAD = \"library.so\"",
        ));
    }

    #[test]
    fn rejects_invalid_status_and_nonzero_pipe_expectations() {
        for status in [-1, 256] {
            rejects(&MINIMAL.replace(
                "expected_status = 0",
                &format!("expected_status = {status}"),
            ));
        }
        let pipe = MINIMAL.replace("kind = \"null\"", "kind = \"pipe\"\ndataset = \"tiny\"");
        assert!(parse_suite(&pipe).is_ok());
        rejects(&pipe.replace("expected_status = 0", "expected_status = 1"));
        assert!(
            parse_suite(&MINIMAL.replace("expected_status = 0", "expected_status = 1")).is_ok()
        );
    }

    #[test]
    fn validates_io_references_for_direct_rust_callers() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        let case = suite.cases.first_mut().ok_or("fixture case absent")?;
        case.io.stdin = StdinPolicy::RegularFile {
            dataset: "absent".into(),
        };
        check_equal(validate_suite(&suite).is_err(), true)?;
        let case = suite.cases.first_mut().ok_or("fixture case absent")?;
        case.io.stdin = StdinPolicy::Null {};
        case.io.stdout = StdoutPolicy::ScratchFile {
            path: "../outside".into(),
        };
        check_equal(validate_suite(&suite).is_err(), true)?;
        Ok(())
    }

    #[test]
    fn validates_profile_overrides_without_bypassing_base_constraints() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .cases
            .first_mut()
            .ok_or("fixture case absent")?
            .profiles
            .insert(
                MeasurementProfile::Smoke,
                CaseOverride {
                    argv: Some(vec!["@input:absent".into()]),
                    ..CaseOverride::default()
                },
            );
        check_equal(validate_suite(&suite).is_err(), true)?;
        suite
            .cases
            .first_mut()
            .ok_or("fixture case absent")?
            .profiles
            .clear();
        suite
            .datasets
            .first_mut()
            .ok_or("fixture dataset absent")?
            .profiles
            .insert(
                MeasurementProfile::Smoke,
                DatasetRecipe {
                    argv: vec!["text".into(), "@output".into()],
                    checks: vec![CorrectnessRule::TextShape {
                        records: 2,
                        words_per_record: 1,
                        word_length: 4,
                    }],
                },
            );
        check_equal(validate_suite(&suite).is_err(), true)?;
        Ok(())
    }

    #[test]
    fn validates_mutation_paths_and_work_counts() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .cases
            .first_mut()
            .ok_or("fixture case absent")?
            .mutation = MutationSetup::Directories {
            paths: vec!["a".into(), "a".into()],
        };
        check_equal(validate_suite(&suite).is_err(), true)?;
        let case = suite.cases.first_mut().ok_or("fixture case absent")?;
        case.mutation = MutationSetup::None {};
        case.work = Some(Work {
            amount: 0,
            unit: WorkUnit::Bytes,
        });
        check_equal(validate_suite(&suite).is_err(), true)?;
        Ok(())
    }

    #[test]
    fn validates_exact_shape_sizes_for_base_and_every_profile() -> TestResult {
        for checks in [
            vec![CorrectnessRule::TextShape {
                records: u64::MAX,
                words_per_record: 1,
                word_length: 4,
            }],
            vec![CorrectnessRule::Records {
                records: vec!["a".into()],
                repeat: u64::MAX,
                cycles: 1,
            }],
            vec![
                CorrectnessRule::BytePattern {
                    bytes: 10,
                    pattern_hex: "00".into(),
                },
                CorrectnessRule::BytePattern {
                    bytes: 11,
                    pattern_hex: "00".into(),
                },
            ],
        ] {
            let mut suite = parse_suite(MINIMAL)?;
            suite
                .datasets
                .first_mut()
                .ok_or("fixture dataset absent")?
                .checks = checks.clone();
            check_equal(
                validate_suite(&suite).err().map(|error| error.kind()),
                Some(ErrorKind::InvalidSuite),
            )?;
            for profile in [MeasurementProfile::Full, MeasurementProfile::Smoke] {
                let mut suite = parse_suite(MINIMAL)?;
                let dataset = suite.datasets.first_mut().ok_or("fixture dataset absent")?;
                dataset.profiles.insert(
                    profile,
                    DatasetRecipe {
                        argv: dataset.argv.clone(),
                        checks: checks.clone(),
                    },
                );
                check_equal(
                    validate_suite(&suite).err().map(|error| error.kind()),
                    Some(ErrorKind::InvalidSuite),
                )?;
            }
        }
        Ok(())
    }

    #[test]
    fn restricts_dataset_assertions_without_restricting_case_assertions() -> TestResult {
        for rule in [
            CorrectnessRule::Literal {
                stream: Stream::Stdout,
                text: "literal".into(),
            },
            CorrectnessRule::Hex {
                stream: Stream::Stderr,
                hex: "00ff".into(),
            },
            CorrectnessRule::EmptyStderr {},
            CorrectnessRule::TailSlice {
                dataset: "tiny".into(),
                unit: crate::TailUnit::Bytes,
                count: 1,
                stream: Stream::Stdout,
            },
            CorrectnessRule::DirectoryTree {
                paths: vec!["a/b".into()],
                compare_mode_to: None,
            },
            CorrectnessRule::Comparator {
                target: crate::ComparisonTarget::Reference,
                stream: Stream::Stdout,
            },
        ] {
            let mut suite = parse_suite(MINIMAL)?;
            suite
                .cases
                .first_mut()
                .ok_or("fixture case absent")?
                .correctness = vec![rule.clone()];
            validate_suite(&suite)?;
            let mut invalid = suite.clone();
            invalid
                .datasets
                .first_mut()
                .ok_or("fixture dataset absent")?
                .checks = vec![rule.clone()];
            check_equal(
                validate_suite(&invalid).err().map(|error| error.kind()),
                Some(ErrorKind::InvalidSuite),
            )?;
            for profile in [MeasurementProfile::Full, MeasurementProfile::Smoke] {
                let mut invalid = suite.clone();
                let dataset = invalid
                    .datasets
                    .first_mut()
                    .ok_or("fixture dataset absent")?;
                dataset.profiles.insert(
                    profile,
                    DatasetRecipe {
                        argv: dataset.argv.clone(),
                        checks: vec![rule.clone()],
                    },
                );
                check_equal(
                    validate_suite(&invalid).err().map(|error| error.kind()),
                    Some(ErrorKind::InvalidSuite),
                )?;
            }
        }
        Ok(())
    }

    #[test]
    fn requires_independent_dataset_checks_and_case_assertions() -> TestResult {
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .datasets
            .first_mut()
            .ok_or("fixture dataset absent")?
            .checks
            .clear();
        check_equal(validate_suite(&suite).is_err(), true)?;
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .cases
            .first_mut()
            .ok_or("fixture case absent")?
            .correctness
            .clear();
        check_equal(validate_suite(&suite).is_err(), true)?;
        let mut suite = parse_suite(MINIMAL)?;
        suite
            .datasets
            .first_mut()
            .ok_or("fixture dataset absent")?
            .checks = vec![CorrectnessRule::Comparator {
            target: crate::ComparisonTarget::Reference,
            stream: Stream::Stdout,
        }];
        check_equal(validate_suite(&suite).is_err(), true)?;
        Ok(())
    }

    #[test]
    fn validates_correctness_rule_parameters() -> TestResult {
        for rule in [
            CorrectnessRule::Hex {
                stream: Stream::Stdout,
                hex: "0".into(),
            },
            CorrectnessRule::BytePattern {
                bytes: 0,
                pattern_hex: String::new(),
            },
            CorrectnessRule::TextShape {
                records: 2,
                words_per_record: 1,
                word_length: 0,
            },
            CorrectnessRule::Records {
                records: vec!["a\nb".into()],
                repeat: 1,
                cycles: 1,
            },
            CorrectnessRule::Records {
                records: vec![String::new()],
                repeat: 0,
                cycles: 1,
            },
            CorrectnessRule::TailSlice {
                dataset: "absent".into(),
                unit: crate::TailUnit::Bytes,
                count: 1,
                stream: Stream::Stdout,
            },
            CorrectnessRule::DirectoryTree {
                paths: vec!["../outside".into()],
                compare_mode_to: None,
            },
        ] {
            let mut suite = parse_suite(MINIMAL)?;
            suite
                .cases
                .first_mut()
                .ok_or("fixture case absent")?
                .correctness = vec![rule];
            check_equal(validate_suite(&suite).is_err(), true)?;
        }
        Ok(())
    }

    #[test]
    fn rejects_empty_or_nonportable_ids_and_build_values() {
        for (needle, replacement) in [
            ("tailr-minimal", ""),
            ("tailr-minimal", "suite/name"),
            ("id = \"tiny\"", "id = \"你好\""),
            ("id = \"last-line\"", "id = \"\""),
            (
                "purpose = \"Read the final line from a tiny regular file\"",
                "purpose = \" \"",
            ),
            ("[environment]", "[build]\ntoolchain = \"\"\n[environment]"),
            ("features = []", "features = [\"\"]"),
        ] {
            rejects(&MINIMAL.replace(needle, replacement));
        }
    }
    #[test]
    fn rejects_explicit_early_exit_pipe_policy() {
        rejects(&MINIMAL.replace(
            "kind = \"null\"",
            "kind = \"pipe\"\ndataset = \"tiny\"\nallow_early_exit = true",
        ));
    }
}
