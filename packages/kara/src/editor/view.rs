use super::{ScreenPosition, Size};
pub(super) mod welcome_message;

#[derive(Default)]
pub struct View {
    pub size: Size,
    pub scroll_offset: ScreenPosition,
    preserve_scroll: bool,
}

impl View {
    pub const fn resize(&mut self, to: Size) {
        self.size = to;
    }

    pub const fn restore(&mut self, offset: ScreenPosition) {
        self.scroll_offset = offset;
        self.preserve_scroll = true;
    }

    pub const fn center(&mut self, position: ScreenPosition) {
        self.scroll_offset = ScreenPosition {
            x: position.x.saturating_sub(self.size.width / 2),
            y: position.y.saturating_sub(self.size.height / 2),
        };
        self.preserve_scroll = true;
    }

    pub fn scroll_into_view(&mut self, current: ScreenPosition) {
        if std::mem::take(&mut self.preserve_scroll) {
            return;
        }
        self.scroll_offset.x = scroll_axis(self.scroll_offset.x, current.x, self.size.width);
        self.scroll_offset.y = scroll_axis(self.scroll_offset.y, current.y, self.size.height);
    }
}

fn scroll_axis(offset: usize, caret: usize, size: usize) -> usize {
    let margin = 5.min(size.saturating_sub(1) / 2);
    if caret < offset.saturating_add(margin) {
        caret.saturating_sub(margin)
    } else if caret >= offset.saturating_add(size).saturating_sub(margin) {
        caret
            .saturating_add(1)
            .saturating_sub(size.saturating_sub(margin))
    } else {
        offset
    }
}
