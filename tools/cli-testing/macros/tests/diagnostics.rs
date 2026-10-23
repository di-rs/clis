#[test]
fn unsupported_attributes_and_signatures_have_actionable_diagnostics() {
    trybuild::TestCases::new().compile_fail("tests/ui/*.rs");
}
