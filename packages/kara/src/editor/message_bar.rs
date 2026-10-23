use std::{
    fmt::Display,
    time::{Duration, Instant},
};

const MESSAGE_DURATION: Duration = Duration::new(5, 0);

struct Message {
    content: String,
    time: Instant,
}

impl Message {
    fn new(content: String) -> Self {
        Self {
            content,
            time: Instant::now(),
        }
    }

    fn is_expired(&self) -> bool {
        Instant::now().duration_since(self.time) > MESSAGE_DURATION
    }
}

impl Default for Message {
    fn default() -> Self {
        Self {
            content: String::new(),
            time: Instant::now(),
        }
    }
}

impl Display for Message {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.is_expired() {
            write!(f, "")
        } else {
            write!(f, "{}", self.content)
        }
    }
}

#[derive(Default)]
pub struct MessageBar {
    current_message: Message,
}

impl MessageBar {
    pub fn set(&mut self, new_message: String) {
        self.current_message = Message::new(new_message);
    }

    pub fn clear(&mut self) {
        self.set(String::new());
    }

    pub fn text(&self) -> String {
        self.current_message.to_string()
    }
}
