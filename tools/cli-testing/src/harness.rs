use crate::{Context, TestContext, catalog::Execution};
use std::process::ExitCode;

/// Run catalog cases as individually selectable Cargo/nextest tests.
/// Catalog errors become a failing named test, never an empty successful suite.
pub fn run_catalog(context: &TestContext) -> ExitCode {
    let arguments = libtest_mimic::Arguments::from_args();
    let tests = match context.suite() {
        Ok(suite) => suite
            .cases
            .iter()
            .filter(|case| case.execution == Execution::Toml)
            .map(|case| {
                let context = context.clone();
                let suite = suite.clone();
                let case = case.clone();
                libtest_mimic::Trial::test(case.name.clone(), move || {
                    Context::prepare(context, &suite, &case)
                        .and_then(|mut context| context.check())
                        .map_err(|error| format!("{error:?}").into())
                })
            })
            .collect(),
        Err(error) => vec![libtest_mimic::Trial::test("catalog_is_valid", move || {
            Err(format!("{error:?}").into())
        })],
    };
    libtest_mimic::run(&arguments, tests).exit_code()
}
