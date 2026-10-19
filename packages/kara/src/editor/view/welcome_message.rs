use crate::editor::{APP_NAME, Size};

const VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct WelcomeMessage;

impl WelcomeMessage {
    pub fn draw(size: &Size) -> Option<String> {
        let Size { width, .. } = *size;

        if width == 0 {
            return None;
        }

        let welcome_message = format!("{APP_NAME} v{VERSION}");
        let message_len = welcome_message.len();
        let remaining_width = width.saturating_sub(1);

        if remaining_width < message_len {
            return Some("~".to_owned());
        }

        let message = format!("{:<1}{:^remaining_width$}", "~", welcome_message);
        Some(message)
    }
}
