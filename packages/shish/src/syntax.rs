//! Syntax inspection only; the runtime owns evaluation.

pub fn parse(input: &str) -> Result<brush_parser::ast::Program, brush_parser::ParseError> {
    brush_parser::Parser::builder()
        .posix_mode(true)
        .sh_mode(true)
        .enable_extended_globbing(false)
        .build(input.as_bytes())
        .parse_program()
}
