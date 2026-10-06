#[test]
fn malformed_comprehensions_produce_diagnostics() {
    trybuild::TestCases::new().compile_fail("tests/ui/*.rs");
}
