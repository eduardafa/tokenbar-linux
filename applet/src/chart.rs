//! CodexBar-style daily history chart: one bar per day in the provider colour,
//! the peak day capped in yellow, hovering a bar selects that day.

use cosmic::iced::mouse;
use cosmic::iced::{Color, Point, Rectangle, Renderer, Size};
use cosmic::widget::canvas::{self, Action, Event, Frame, Geometry, Path};

pub struct DayBars<Message> {
    pub values: Vec<f64>,
    pub selected: Option<usize>,
    pub color: Color,
    pub on_hover: fn(usize) -> Message,
}

#[derive(Default)]
pub struct HoverState {
    last: Option<usize>,
}

const CAP_COLOR: Color = Color::from_rgb(1.0, 0.80, 0.0); // systemYellow-ish
const GAP: f32 = 2.0;

impl<Message: Clone> DayBars<Message> {
    fn slot(&self, width: f32) -> f32 {
        width / self.values.len().max(1) as f32
    }
}

impl<Message: Clone> canvas::Program<Message, cosmic::Theme, Renderer> for DayBars<Message> {
    type State = HoverState;

    fn update(
        &self,
        state: &mut HoverState,
        event: &Event,
        bounds: Rectangle,
        cursor: mouse::Cursor,
    ) -> Option<Action<Message>> {
        if !matches!(event, Event::Mouse(mouse::Event::CursorMoved { .. })) || self.values.is_empty() {
            return None;
        }
        let pos = cursor.position_in(bounds)?;
        let i = ((pos.x / self.slot(bounds.width)) as usize).min(self.values.len() - 1);
        if state.last == Some(i) {
            return None;
        }
        state.last = Some(i);
        Some(Action::publish((self.on_hover)(i)))
    }

    fn draw(
        &self,
        _state: &HoverState,
        renderer: &Renderer,
        theme: &cosmic::Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry<Renderer>> {
        let mut frame = Frame::new(renderer, bounds.size());
        let (w, h) = (bounds.width, bounds.height);
        let fg: Color = theme.cosmic().background(theme.transparent).on.into();

        // baseline
        frame.fill_rectangle(Point::new(0.0, h - 1.0), Size::new(w, 1.0), Color { a: 0.15, ..fg });

        let peak = self.values.iter().cloned().fold(0.0_f64, f64::max);
        if peak <= 0.0 {
            return vec![frame.into_geometry()];
        }
        let peak_idx = self.values.iter().position(|v| *v == peak);
        let slot = self.slot(w);
        let bw = (slot - GAP).max(1.0);
        let usable = h - 2.0;

        for (i, v) in self.values.iter().enumerate() {
            let x = i as f32 * slot + GAP / 2.0;
            // selected-day band behind the bar
            if self.selected == Some(i) {
                frame.fill_rectangle(Point::new(i as f32 * slot, 0.0), Size::new(slot, h - 1.0), Color { a: 0.08, ..fg });
            }
            if *v <= 0.0 {
                continue;
            }
            let bh = ((*v / peak) as f32 * usable).max(2.0);
            let y = h - 1.0 - bh;
            let radius = (bw / 2.0).min(3.0);
            let bar = Path::new(|b| {
                b.rounded_rectangle(Point::new(x, y), Size::new(bw, bh), [radius, radius, 0.0, 0.0].into())
            });
            let alpha = if self.selected.is_none() || self.selected == Some(i) { 1.0 } else { 0.55 };
            frame.fill(&bar, Color { a: alpha, ..self.color });
            if Some(i) == peak_idx {
                let cap = bh.min(4.0);
                let cap_path = Path::new(|b| {
                    b.rounded_rectangle(Point::new(x, y), Size::new(bw, cap), [radius, radius, 0.0, 0.0].into())
                });
                frame.fill(&cap_path, CAP_COLOR);
            }
        }
        vec![frame.into_geometry()]
    }

    fn mouse_interaction(&self, _s: &HoverState, bounds: Rectangle, cursor: mouse::Cursor) -> mouse::Interaction {
        if cursor.is_over(bounds) {
            mouse::Interaction::Pointer
        } else {
            mouse::Interaction::default()
        }
    }
}

