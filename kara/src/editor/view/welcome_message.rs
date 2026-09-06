use crate::editor::Size;

const NAME: &str = env!("CARGO_PKG_NAME");
const VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct WelcomeMessage;

impl WelcomeMessage {
    pub fn draw(size: &Size) -> (usize, String) {
        let Size { height, width } = *size;

        let welcome_message = format!("{NAME} v{VERSION}");
        let message_len = welcome_message.len();

        let y = height / 3;
        let padding = width.saturating_sub(message_len).saturating_sub(1) / 2;

        let mut message = format!("~{}{}", " ".repeat(padding), welcome_message);
        message.truncate(width);

        (y, message)
    }
}
