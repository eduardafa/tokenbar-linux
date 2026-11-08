//! COSMIC panel applet modelled on CodexBar: "session% · weekly%" in the panel,
//! a CodexBar-style menu in the popup, and a daily cost history view.

mod chart;
mod data;

use std::time::{Duration, Instant};

use cosmic::app::{Core, Task};
use cosmic::iced::window::Id;
use cosmic::iced::{Alignment, Color, Length, Rectangle, Subscription};
use cosmic::surface::action::{app_popup, destroy_popup};
use cosmic::widget::{self, button, container, divider, text, Column, Row};
use cosmic::Element;

use data::{fmt_duration, fmt_money, fmt_tokens, Pace, Provider, Window};

const APP_ID: &str = "io.github.eduardafranca.CosmicAppletTokenMonitor";
static AUTOSIZE_ID: std::sync::LazyLock<widget::Id> =
    std::sync::LazyLock::new(|| widget::Id::new("autosize-main"));
const REFRESH: Duration = Duration::from_secs(120);
const WIDTH: f32 = 340.0;

#[derive(Debug, Clone, Copy, PartialEq)]
enum Metric {
    Cost,
    Tokens,
}

struct Applet {
    core: Core,
    popup: Option<Id>,
    providers: Vec<Provider>,
    error: Option<String>,
    loading: bool,
    updated: Option<Instant>,
    selected: usize,
    metric: Metric,
    day: Option<usize>,
}

#[derive(Debug, Clone)]
enum Message {
    Tick,
    Refresh,
    Loaded(Result<Vec<Provider>, String>),
    SelectProvider(usize),
    SetMetric(Metric),
    HoverDay(usize),
    OpenDashboard,
    OpenSettings,
    OpenUrl(String),
    PopupClosed(Id),
    Surface(cosmic::surface::Action<Message>),
}

/// Provider brand colours (CodexBar uses the provider colour for bars).
fn brand(id: &str) -> Color {
    match id {
        "claude" => Color::from_rgb8(0xD9, 0x77, 0x57),
        "codex" => Color::from_rgb8(0x10, 0xA3, 0x7F),
        _ => Color::from_rgb8(0x39, 0x87, 0xE5),
    }
}

fn secondary<'a>(t: impl Into<std::borrow::Cow<'a, str>> + 'a) -> widget::Text<'a, cosmic::Theme> {
    text::caption(t).class(cosmic::theme::Text::Custom(|theme| cosmic::iced::widget::text::Style {
        color: Some(Color { a: 0.65, ..theme.cosmic().background(theme.transparent).on.into() }),
        ..Default::default()
    }))
}

fn spread<'a>(left: Element<'a, Message>, right: Element<'a, Message>) -> Element<'a, Message> {
    Row::new()
        .align_y(Alignment::Center)
        .push(container(left).width(Length::Fill))
        .push(right)
        .into()
}

fn bar<'a>(used: f64, color: Color, marker: Option<f64>) -> Element<'a, Message> {
    let b = widget::progress_bar::determinate_linear((used / 100.0) as f32)
        .width(Length::Fill)
        .girth(Length::Fixed(6.0))
        .bar_color(color);
    match marker {
        Some(m) => b.markers(vec![(m / 100.0) as f32]).into(),
        None => b.into(),
    }
}

/// CodexBar: "Pace: Behind (-42%) · Lasts to reset"
fn pace_line(w: &Window) -> Option<String> {
    let expected = w.expected()?;
    let delta = w.used_percent - expected;
    let head = if delta.abs() < 2.0 {
        "On pace".to_string()
    } else if delta < 0.0 {
        format!("Behind ({:.0}%)", delta)
    } else {
        format!("Ahead (+{:.0}%)", delta)
    };
    let tail = match w.pace()? {
        Pace::Lasts => "Lasts to reset".to_string(),
        Pace::RunsOutIn(s) => format!("Runs out in {}", fmt_duration(s)),
    };
    Some(format!("Pace: {head} · {tail}"))
}

/// Compact menu row (libcosmic's menu_button pads 24px each side; too roomy here).
fn menu_item<'a>(icon: &'static str, label: &'a str, msg: Message) -> Element<'a, Message> {
    button::custom(
        Row::new()
            .spacing(8)
            .align_y(Alignment::Center)
            .push(widget::icon::from_name(icon).size(16))
            .push(text::body(label)),
    )
    .class(cosmic::theme::Button::AppletMenu)
    .padding([4, 6])
    .width(Length::Fill)
    .on_press(msg)
    .into()
}

fn spawn_detached(cmd: &str) {
    let _ = std::process::Command::new("sh")
        .arg("-c")
        .arg(format!("setsid {cmd} >/dev/null 2>&1 &"))
        .status();
}

impl Applet {
    fn load(&mut self) -> Task<Message> {
        if self.loading {
            return Task::none();
        }
        self.loading = true;
        Task::perform(data::fetch(), |r| cosmic::Action::App(Message::Loaded(r)))
    }

    fn current(&self) -> Option<&Provider> {
        self.providers.get(self.selected).or_else(|| self.providers.first())
    }

    /// Text shown in the panel, e.g. "21% · 4%".
    fn panel_label(&self) -> String {
        if let Some(p) = self.providers.iter().find(|p| !p.windows.is_empty()) {
            let pct = |k: &str| p.window(k).map(|w| format!("{:.0}%", w.used_percent));
            let parts: Vec<String> = [pct("session"), pct("weekly")].into_iter().flatten().collect();
            let label = if parts.is_empty() {
                format!("{:.0}%", p.windows[0].used_percent)
            } else {
                parts.join(" · ")
            };
            let hot = p.windows.iter().any(|w| w.used_percent >= 90.0);
            let incident = self.providers.iter().any(|p| p.incident().is_some());
            return format!("{}{}", if hot || incident { "⚠ " } else { "" }, label);
        }
        if self.loading && self.updated.is_none() {
            "…".into()
        } else {
            "AI –".into()
        }
    }

    fn updated_label(&self) -> String {
        match (self.loading, self.updated) {
            (true, _) => "Refreshing…".into(),
            (_, Some(t)) if t.elapsed().as_secs() >= 60 => {
                format!("Updated {} ago", fmt_duration(t.elapsed().as_secs() as i64))
            }
            (_, Some(_)) => "Updated just now".into(),
            _ => String::new(),
        }
    }

    fn popup_view(&self) -> Element<'_, Message> {
        container(self.main_page()).width(Length::Fixed(WIDTH)).into()
    }

    // ---------------- main menu ----------------
    fn main_page(&self) -> Element<'_, Message> {
        let mut col = Column::new().spacing(14).padding([12, 16]);

        // provider switcher (only with more than one provider)
        if self.providers.len() > 1 {
            let mut tabs = Row::new().spacing(6);
            for (i, p) in self.providers.iter().enumerate() {
                let used = p.windows.first().map(|w| w.used_percent).unwrap_or(0.0);
                let tab = Column::new()
                    .spacing(4)
                    .align_x(Alignment::Center)
                    .push(text::body(p.name.clone()))
                    .push(bar(used, brand(&p.id), None));
                let b = button::custom(tab)
                    .padding([6, 10])
                    .width(Length::Fill)
                    .on_press(Message::SelectProvider(i))
                    .class(if i == self.selected {
                        cosmic::theme::Button::Suggested
                    } else {
                        cosmic::theme::Button::Text
                    });
                tabs = tabs.push(b);
            }
            col = col.push(tabs).push(divider::horizontal::default());
        }

        let Some(p) = self.current() else {
            let msg = self.error.clone().unwrap_or_else(|| {
                if self.loading {
                    "Loading…".into()
                } else {
                    "No providers found. Log in with `claude` or `codex`.".into()
                }
            });
            return col.push(text::body(msg)).push(self.footer(None)).into();
        };
        let color = brand(&p.id);

        // header: "Claude ........ Pro" / account email / "Updated just now"
        let mut header = Column::new().spacing(2).push(spread(
            text::title4(p.name.clone()).into(),
            text::body(p.plan.clone().unwrap_or_default()).into(),
        ));
        if let Some(acc) = &p.account {
            header = header.push(text::body(acc.clone()));
        }
        col = col.push(header.push(secondary(self.updated_label())));
        if let Some(inc) = p.incident() {
            col = col.push(text::body(format!("⚠ {inc}")));
        }
        if let Some(e) = &p.error {
            col = col.push(secondary(e.clone()));
        }
        col = col.push(divider::horizontal::default());

        // usage windows
        if !p.windows.is_empty() {
            let mut windows = Column::new().spacing(16);
            for w in &p.windows {
                let mut wc = Column::new()
                    .spacing(6)
                    .push(text::heading(w.label.clone()))
                    .push(bar(w.used_percent, color, None))
                    .push(spread(
                        text::body(format!("{:.0}% used", w.used_percent)).into(),
                        secondary(w.reset_in().map(|s| format!("Resets in {}", fmt_duration(s))).unwrap_or_default())
                            .into(),
                    ));
                if w.key != "session" {
                    if let Some(line) = pace_line(w) {
                        wc = wc.push(secondary(line));
                    }
                }
                windows = windows.push(wc);
            }
            col = col.push(windows);
        }
        if let Some(c) = &p.credits {
            col = col.push(divider::horizontal::default()).push(
                Column::new()
                    .spacing(6)
                    .push(text::heading("Extra usage"))
                    .push(text::body(c.clone())),
            );
        }

        // Cost: big Today / Last 30 days figures, then the daily history inline
        if let Some(c) = p.cost.as_ref().filter(|c| c.month_tokens > 0) {
            col = col
                .push(divider::horizontal::default())
                .push(
                    Row::new()
                        .spacing(12)
                        .push(stat("Today", fmt_money(c.today_cost), fmt_tokens(c.today_tokens)))
                        .push(stat("Last 30 days", fmt_money(c.month_cost), fmt_tokens(c.month_tokens))),
                )
                .push(self.daily_section(p, c));
        }

        col.push(self.footer(Some(p))).into()
    }

    fn footer<'a>(&'a self, p: Option<&'a Provider>) -> Element<'a, Message> {
        let mut items = Column::new().spacing(0);
        if let Some(url) = p.and_then(|p| p.dashboard_url.clone()) {
            items = items.push(menu_item("view-paged-symbolic", "Usage Dashboard", Message::OpenUrl(url)));
        }
        if let Some(url) = p.and_then(|p| p.status.as_ref()).map(|s| s.url.clone()).filter(|u| !u.is_empty()) {
            items = items.push(menu_item("network-transmit-receive-symbolic", "Status Page", Message::OpenUrl(url)));
        }
        items = items
            .push(menu_item("view-refresh-symbolic", "Refresh", Message::Refresh))
            .push(menu_item("utilities-system-monitor-symbolic", "Open Dashboard…", Message::OpenDashboard))
            .push(menu_item("emblem-system-symbolic", "Settings…", Message::OpenSettings));
        Column::new().spacing(6).push(divider::horizontal::default()).push(items).into()
    }

    // ---------------- daily cost history (shown inline) ----------------
    fn daily_section<'a>(&'a self, p: &'a Provider, cost: &'a data::Cost) -> Element<'a, Message> {
        let mut col = Column::new().spacing(8);
        let toggle = |m: Metric, label: &'static str| {
            button::text(label)
                .on_press(Message::SetMetric(m))
                .class(if self.metric == m {
                    cosmic::theme::Button::Suggested
                } else {
                    cosmic::theme::Button::Text
                })
        };
        col = col.push(
            Row::new()
                .align_y(Alignment::Center)
                .spacing(4)
                .push(text::heading("Daily cost").width(Length::Fill))
                .push(toggle(Metric::Cost, "Cost"))
                .push(toggle(Metric::Tokens, "Tokens")),
        );
        if cost.daily.is_empty() {
            return col.push(secondary("No data available")).into();
        }
        let days = &cost.daily;
        let value = |d: &data::Day| if self.metric == Metric::Cost { d.cost } else { d.tokens as f64 };
        let fmt_value = |v: f64| {
            if self.metric == Metric::Cost {
                format!("${v:.0}")
            } else {
                fmt_tokens(v as u64)
            }
        };
        let values: Vec<f64> = days.iter().map(value).collect();
        let peak = values.iter().cloned().fold(0.0_f64, f64::max);
        let selected = self
            .day
            .filter(|i| *i < days.len())
            .or_else(|| days.iter().rposition(|d| d.cost > 0.0 || d.tokens > 0));

        // chart + y-axis labels on the right (like Swift Charts' trailing axis)
        let canvas = widget::canvas(chart::DayBars {
            values,
            selected,
            color: brand(&p.id),
            on_hover: Message::HoverDay,
        })
        .width(Length::Fill)
        .height(Length::Fixed(130.0));
        let y_axis = Column::new()
            .height(Length::Fixed(130.0))
            .width(Length::Fixed(40.0))
            .align_x(Alignment::End)
            .push(secondary(fmt_value(peak)))
            .push(widget::space().height(Length::Fill))
            .push(secondary(fmt_value(peak / 2.0)))
            .push(widget::space().height(Length::Fill))
            .push(secondary(fmt_value(0.0)));
        col = col.push(Row::new().spacing(6).push(canvas).push(y_axis));

        // x-axis: first / middle / last date
        let mid = days.len() / 2;
        col = col.push(
            Row::new()
                .padding([0, 46, 0, 0])
                .push(secondary(days[0].label()))
                .push(widget::space().width(Length::Fill))
                .push(secondary(days[mid].label()))
                .push(widget::space().width(Length::Fill))
                .push(secondary(days[days.len() - 1].label())),
        );

        // detail for the hovered day: "Nov 8: $ 15.60 · 22.4M tokens" + one row per model
        match selected.map(|i| &days[i]) {
            Some(d) => {
                col = col.push(secondary(format!(
                    "{}: {} · {} tokens",
                    d.label(),
                    fmt_money(d.cost),
                    fmt_tokens(d.tokens)
                )));
                let base = brand(&p.id);
                for (i, m) in d.models.iter().enumerate() {
                    let accent = Color { a: (1.0 - 0.2 * i as f32).max(0.35), ..base };
                    let swatch = container(widget::space().width(Length::Fixed(4.0)).height(Length::Fixed(30.0)))
                        .class(cosmic::theme::Container::custom(move |_| container::Style {
                            background: Some(accent.into()),
                            border: cosmic::iced::Border { radius: 2.0.into(), ..Default::default() },
                            ..Default::default()
                        }));
                    col = col.push(
                        Row::new()
                            .spacing(8)
                            .align_y(Alignment::Center)
                            .push(swatch)
                            .push(
                                Column::new()
                                    .push(text::caption(m.name.clone()))
                                    .push(secondary(format!("{} · {} tokens", fmt_money(m.cost), fmt_tokens(m.tokens))))
                                    .push(secondary(format!(
                                        "in {} · out {} · cache read {} · write {}",
                                        fmt_tokens(m.input),
                                        fmt_tokens(m.output),
                                        fmt_tokens(m.cache_read),
                                        fmt_tokens(m.cache_write)
                                    ))),
                            ),
                    );
                }
            }
            None => col = col.push(secondary("Hover a bar for details")),
        }

        if !cost.models.is_empty() {
            col = col.push(divider::horizontal::default()).push(text::caption("Last 30 days by model"));
            let mut models = Column::new().spacing(2);
            for m in &cost.models {
                models = models.push(spread(
                    text::caption(m.name.clone()).into(),
                    secondary(format!("{} · {} tokens", fmt_money(m.cost), fmt_tokens(m.tokens))).into(),
                ));
            }
            col = col.push(models);
        }
        col = col.push(secondary("API-equivalent estimate from local Claude Code logs"));
        col.into()
    }
}

fn stat<'a>(label: &'a str, value: String, tokens: String) -> Element<'a, Message> {
    Column::new()
        .width(Length::Fill)
        .push(secondary(label))
        .push(text::title2(value))
        .push(secondary(format!("{tokens} tokens")))
        .into()
}

impl cosmic::Application for Applet {
    type Executor = cosmic::executor::Default;
    type Flags = ();
    type Message = Message;
    const APP_ID: &'static str = APP_ID;

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, _flags: ()) -> (Self, Task<Message>) {
        let mut app = Applet {
            core,
            popup: None,
            providers: vec![],
            error: None,
            loading: false,
            updated: None,
            selected: 0,
            metric: Metric::Cost,
            day: None,
        };
        let task = app.load();
        (app, task)
    }

    fn on_close_requested(&self, id: Id) -> Option<Message> {
        Some(Message::PopupClosed(id))
    }

    fn subscription(&self) -> Subscription<Message> {
        cosmic::iced::time::every(REFRESH).map(|_| Message::Tick)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::Tick | Message::Refresh => return self.load(),
            Message::Loaded(res) => {
                self.loading = false;
                match res {
                    Ok(p) => {
                        self.providers = p;
                        self.error = None;
                        self.updated = Some(Instant::now());
                        if self.selected >= self.providers.len() {
                            self.selected = 0;
                        }
                    }
                    Err(e) => self.error = Some(e),
                }
            }
            Message::SelectProvider(i) => {
                self.selected = i;
                self.day = None;
            }
            Message::SetMetric(m) => self.metric = m,
            Message::HoverDay(i) => self.day = Some(i),
            Message::OpenDashboard => {
                spawn_detached(&format!("'{}' --no-tray", data::backend().display()));
            }
            Message::OpenSettings => {
                spawn_detached(&format!("'{}' --no-tray --preferences", data::backend().display()));
            }
            Message::OpenUrl(url) => spawn_detached(&format!("xdg-open '{url}'")),
            Message::PopupClosed(id) => {
                if self.popup == Some(id) {
                    self.popup = None;
                    self.day = None;
                }
            }
            Message::Surface(a) => return cosmic::task::message(cosmic::Action::Surface(a)),
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let have_popup = self.popup;
        // Same structure as COSMIC's clock applet: text + a spacer that fixes the height.
        let applet = &self.core.applet;
        let height = applet.suggested_size(true).1 + 2 * applet.suggested_padding(true).1;
        let content = Row::new()
            .align_y(Alignment::Center)
            .push(applet.text(self.panel_label()))
            .push(container(widget::space().height(Length::Fixed(f32::from(height)))));
        let btn = button::custom(content)
            .padding([0, applet.suggested_padding(true).0])
            .class(cosmic::theme::Button::AppletIcon)
            .on_press_with_rectangle(move |offset, bounds| {
                if let Some(id) = have_popup {
                    Message::Surface(destroy_popup(id))
                } else {
                    Message::Surface(app_popup::<Applet>(
                        |_| Default::default(),
                        move |state: &mut Applet| {
                            let new_id = Id::unique();
                            state.popup = Some(new_id);
                            let mut s = state.core.applet.get_popup_settings(
                                state.core.main_window_id().unwrap(),
                                new_id,
                                None,
                                None,
                                None,
                            );
                            s.positioner.anchor_rect = Rectangle {
                                x: (bounds.x - offset.x) as i32,
                                y: (bounds.y - offset.y) as i32,
                                width: bounds.width as i32,
                                height: bounds.height as i32,
                            };
                            s
                        },
                        Some(Box::new(|state: &Applet| {
                            Element::from(state.core.applet.popup_container(state.popup_view()))
                                .map(cosmic::Action::App)
                        })),
                    ))
                }
            });

        widget::autosize::autosize(btn, AUTOSIZE_ID.clone()).into()
    }

    fn view_window(&self, _id: Id) -> Element<'_, Message> {
        "".into()
    }

    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
}

fn main() -> cosmic::iced::Result {
    cosmic::applet::run::<Applet>(())
}
