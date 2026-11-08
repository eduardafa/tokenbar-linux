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
    pub fn window(&self, key: &str) -> Option<&Window> {
        self.windows.iter().find(|w| w.key == key)
    }

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

pub async fn fetch() -> Result<Vec<Provider>, String> {
    let out = tokio::process::Command::new(backend())
        .args(["usage", "--json"])
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
