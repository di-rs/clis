//! Reusable status for the Sanctuary `true` command.

/// Return the command's status without output, logging, or process termination.
#[must_use]
pub const fn exit_status() -> u8 {
    0
}
