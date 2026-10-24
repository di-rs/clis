use cli_testing_macros::test_case;

struct Context;

#[test_case("case")]
async fn asynchronous(_: &mut Context) {}

#[test_case("case")]
unsafe fn unsafe_function(_: &mut Context) {}

#[test_case("case")]
fn generic<T>(cx: &mut Context) {}

#[test_case("case")]
fn missing_context() {}

#[test_case("case")]
fn extra_context(cx: &mut Context, other: &mut Context) {}

#[test_case("case")]
fn immutable_context(cx: &Context) {}

#[test_case("case")]
fn owned_context(cx: Context) {}

#[test_case("case")]
fn discarded_context(_: &mut Context) {}

#[test_case("case")]
fn wrong_return(cx: &mut Context) -> bool { true }

#[test_case("case")]
#[test]
fn redundant_test(cx: &mut Context) {}

#[test_case("case")]
#[should_panic]
fn expected_panic(cx: &mut Context) {}

#[test_case("case")]
#[cfg_attr(all(), should_panic)]
fn conditional_panic(cx: &mut Context) {}

#[test_case(1)]
fn numeric_id(cx: &mut Context) {}

#[test_case("case")]
fn wrong_result(cx: &mut Context) -> Result<u8, ()> { Ok(1) }

#[test_case("case")]
const fn constant(cx: &mut Context) {}

fn main() {}
