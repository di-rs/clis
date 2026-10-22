//! Syntax inspection only; the runtime owns evaluation.

pub fn parse(
    input: &str,
    bash: bool,
) -> Result<brush_parser::ast::Program, brush_parser::ParseError> {
    brush_parser::Parser::builder()
        .posix_mode(!bash)
        .sh_mode(!bash)
        .enable_extended_globbing(false)
        .build(input.as_bytes())
        .parse_program()
}
