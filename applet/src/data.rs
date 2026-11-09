//! Usage data, read from `tokenbar usage --json` (the Python backend).

use chrono::{DateTime, Utc};
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Clone, Deserialize)]
pub struct Window {
    pub label: String,
    #[serde(default)]
    pub key: String,
    pub used_percent: f64,
    pub resets_at: Option<DateTime<Utc>>,
    pub window_seconds: Option<i64>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct ModelRow {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub cost: f64,
    #[serde(default)]
    pub tokens: u64,
    #[serde(default)]
    pub input: u64,
    #[serde(default)]
    pub output: u64,
    #[serde(default)]
    pub cache_read: u64,
    #[serde(default)]
    pub cache_write: u64,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Day {
    pub date: String,
    #[serde(default)]
    pub cost: f64,
    #[serde(default)]
    pub tokens: u64,
    #[serde(default)]
    pub models: Vec<ModelRow>,
}

impl Day {
    /// "2026-11-08" -> "Nov 8"
    pub fn label(&self) -> String {
        chrono::NaiveDate::parse_from_str(&self.date, "%Y-%m-%d")
            .map(|d| d.format("%b %-d").to_string())
            .unwrap_or_else(|_| self.date.clone())
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Cost {
    #[serde(default)]
    pub today_cost: f64,
    #[serde(default)]
    pub today_tokens: u64,
    #[serde(default)]
    pub month_cost: f64,
    #[serde(default)]
    pub month_tokens: u64,
    #[serde(default)]
    pub daily: Vec<Day>,
    #[serde(default)]
    pub models: Vec<ModelRow>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Status {
    #[serde(default)]
    pub indicator: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub url: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Provider {
    #[serde(default)]
    pub id: String,
    pub name: String,
    /// When the backend last got fresh data (it may serve cached data while rate limited).
    pub updated_at: Option<DateTime<Utc>>,
    pub account: Option<String>,
    pub plan: Option<String>,
    #[serde(default)]
    pub windows: Vec<Window>,
    pub credits: Option<String>,
    pub cost: Option<Cost>,
    pub status: Option<Status>,
    pub error: Option<String>,
    pub dashboard_url: Option<String>,
}

impl Provider {
    pub fn incident(&self) -> Option<&str> {
        self.status
            .as_ref()
            .filter(|s| !s.indicator.is_empty() && s.indicator != "none")
            .map(|s| s.description.as_str())
    }
}

pub fn fmt_money(v: f64) -> String {
    if v > 0.0 && v < 0.01 {
        "<$0.01".into()
    } else {
        format!("${v:.2}")
    }
}

pub enum Pace {
    Lasts,
    RunsOutIn(i64),
}

impl Window {
    pub fn reset_in(&self) -> Option<i64> {
        self.resets_at.map(|r| (r - Utc::now()).num_seconds().max(0))
    }

    /// Percent an even burn rate would have used by now.
    pub fn expected(&self) -> Option<f64> {
        let total = self.window_seconds? as f64;
        let elapsed = total - self.reset_in()? as f64;
        (elapsed > 0.0).then(|| 100.0 * elapsed / total)
    }

    /// Same projection as the Python side: at the current burn rate, does it last until reset?
    pub fn pace(&self) -> Option<Pace> {
        let total = self.window_seconds? as f64;
        let remaining = self.reset_in()? as f64;
        let elapsed = total - remaining;
        if elapsed <= 60.0 || remaining <= 0.0 {
            return None;
        }
        if self.used_percent <= 0.0 {
            return Some(Pace::Lasts);
        }
        let rate = self.used_percent / elapsed;
        let to_empty = (100.0 - self.used_percent) / rate;
        Some(if to_empty >= remaining {
            Pace::Lasts
        } else {
            Pace::RunsOutIn(to_empty as i64)
        })
    }
}

pub fn fmt_duration(secs: i64) -> String {
    let (d, h, m) = (secs / 86400, (secs % 86400) / 3600, (secs % 3600) / 60);
    if d > 0 {
        format!("{d}d {h}h")
    } else if h > 0 {
        format!("{h}h {m}m")
    } else {
        format!("{m}m")
    }
}

pub fn fmt_tokens(n: u64) -> String {
    let n = n as f64;
    if n >= 1e9 {
        format!("{:.1}B", n / 1e9)
    } else if n >= 1e6 {
        format!("{:.1}M", n / 1e6)
    } else if n >= 1e3 {
        format!("{:.1}K", n / 1e3)
    } else {
        format!("{n}")
    }
}

/// Adaptive refresh delay, same table as CodexBar's AdaptiveRefreshPolicyCore
/// (and tokenbar/schedule.py): 2 min after the popup was opened, 5 min within the
/// hour, 15 min within 4 h, else 30 min; capped at 5 min while coding.
pub fn refresh_delay(since_open: Option<u64>, since_activity: Option<u64>) -> u64 {
    const MIN: u64 = 60;
    let mut d = match since_open {
        Some(s) if s < 5 * MIN => 2 * MIN,
        Some(s) if s < 60 * MIN => 5 * MIN,
        Some(s) if s < 4 * 60 * MIN => 15 * MIN,
        _ => 30 * MIN,
    };
    if since_activity.is_some_and(|s| s < 5 * MIN) {
        d = d.min(5 * MIN);
    }
    d
}

/// Seconds since Claude Code / Codex last wrote a session log (local files only, no API).
pub fn since_coding_activity() -> Option<u64> {
    let home = PathBuf::from(std::env::var_os("HOME")?);
    let codex = std::env::var_os("CODEX_HOME").map(PathBuf::from).unwrap_or_else(|| home.join(".codex"));
    let mut newest: Option<std::time::SystemTime> = None;
    for root in [home.join(".claude/projects"), codex.join("sessions")] {
        let Ok(dirs) = std::fs::read_dir(&root) else { continue };
        for d in dirs.flatten() {
            let mut paths = vec![d.path()];
            if let Ok(inner) = std::fs::read_dir(d.path()) {
                paths.extend(inner.flatten().map(|e| e.path()));
            }
            for p in paths {
                if let Ok(m) = p.metadata().and_then(|m| m.modified()) {
                    newest = Some(newest.map_or(m, |n| n.max(m)));
                }
            }
        }
    }
    newest.and_then(|t| t.elapsed().ok()).map(|d| d.as_secs())
}

/// The installed CLI launcher, falling back to PATH.
pub fn backend() -> PathBuf {
    if let Some(home) = std::env::var_os("HOME") {
        let p = PathBuf::from(home).join(".local/bin/tokenbar");
        if p.exists() {
            return p;
        }
    }
    PathBuf::from("tokenbar")
}

/// How much the backend may hit provider APIs.
#[derive(Debug, Clone, Copy)]
pub enum Mode {
    /// Cached data only (applet start): no API calls.
    Offline,
    /// Popup opened: API call unless the backend's 2-minute cache is fresh.
    Normal,
    /// Refresh button: ignore the 2-minute cache.
    Force,
    /// Scheduled background refresh (cache-only if the user turned background refresh off).
    Background,
}

pub async fn fetch(mode: Mode) -> Result<Vec<Provider>, String> {
    let mut args = vec!["usage", "--json"];
    match mode {
        Mode::Offline => args.push("--offline"),
        Mode::Force => args.push("--refresh"),
        Mode::Background => args.push("--background"),
        Mode::Normal => {}
    }
    if !matches!(mode, Mode::Offline) {
        args.push("--notify"); // every real observation can trigger notifications
    }
    let out = tokio::process::Command::new(backend())
        .args(&args)
        .kill_on_drop(true)
        .output()
        .await
        .map_err(|e| format!("Couldn't run tokenbar: {e}. Run ./install.sh."))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(err.lines().last().unwrap_or("tokenbar failed").to_string());
    }
    serde_json::from_slice(&out.stdout).map_err(|e| format!("Bad data from tokenbar: {e}"))
}
