use cli_testing::catalog::{Case, Execution, Input, Suite, Transform};
use std::fmt::Write as _;

pub(super) fn describe(suite: &Suite, case: &Case) -> String {
    let mut description = String::new();
    let owner = match case.execution {
        Execution::Toml => "toml",
        Execution::Rust => "rust",
    };
    let _ = writeln!(description, "{} [{owner}]", case.name);
    let _ = writeln!(
        description,
        "  candidate: {} {:?}; cwd: {}",
        suite.binary,
        case.args,
        suite.package_dir().display()
    );
    let _ = writeln!(description, "  input dependencies: {:?}", case.inputs);
    let _ = match &case.stdin {
        None => writeln!(description, "  stdin: closed"),
        Some(Input::Pipe {
            text: Some(text), ..
        }) => writeln!(description, "  stdin: pipe, text {text:?}"),
        Some(Input::Pipe { files, .. }) => {
            writeln!(description, "  stdin: pipe, concatenate {files:?}")
        }
        Some(Input::File { path }) => {
            writeln!(description, "  stdin: regular file {}", path.display())
        }
    };
    let _ = writeln!(
        description,
        "  environment overrides: suite {:?}, case {:?}",
        suite.env, case.env
    );
    let _ = writeln!(
        description,
        "  expect: exit {}; stdout {}; stderr {}",
        case.expect.exit,
        stream_description(
            case.expect.stdout.as_deref(),
            case.expect.stdout_file.as_deref()
        ),
        stream_description(
            case.expect.stderr.as_deref(),
            case.expect.stderr_file.as_deref()
        )
    );
    if let Some(name) = &case.origin.reference
        && let Some(reference) = suite.references.get(name)
    {
        let _ = writeln!(
            description,
            "  reference {name}: {} {:?} ({})",
            reference.program.display(),
            case.origin.args.as_ref().unwrap_or(&case.args),
            reference.platform
        );
        let _ = writeln!(
            description,
            "  reference status: {}; environment overrides: {:?}, origin {:?}",
            case.origin.exit.unwrap_or(case.expect.exit),
            reference.env,
            case.origin.env
        );
        if let Some(stderr) = &case.origin.stderr {
            let _ = writeln!(description, "  reference stderr: text {stderr:?}");
        }
    }
    if let Some(reason) = &case.origin.manual {
        let _ = writeln!(description, "  manual expectation: {reason}");
    }
    if let Some(reason) = &case.origin.reason {
        let _ = writeln!(description, "  reference difference: {reason}");
    }
    if let Some(adapt) = &case.adapt {
        let name = match adapt.expected_stdout {
            Transform::Utf8Lossy => "utf8-lossy",
        };
        let _ = writeln!(
            description,
            "  expected stdout adaptation: {name}; {}",
            adapt.reason
        );
    }
    description
}

fn stream_description(text: Option<&str>, file: Option<&std::path::Path>) -> String {
    file.map_or_else(
        || format!("text {:?}", text.unwrap_or_default()),
        |file| format!("file {}", file.display()),
    )
}
