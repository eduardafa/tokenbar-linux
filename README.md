<p align="center"><img src="data/icons/io.github.eduardafranca.TokenBar.svg" width="96" alt=""></p>

# TokenBar

AI coding plan limits and spend in your Linux panel. A [CodexBar](https://codexbar.app) for Linux.

![TokenBar popup in the COSMIC panel](docs/hero.png)

<sub>Screenshots use demo data.</sub>

## What it shows

- **Panel:** the TokenBar icon. Click it for the popup.
- **Popup:** limits with reset countdowns and pace, spend today and over 30 days, and daily cost by model
- **Notifications**, like CodexBar's: usage at 80% and 95%, pace warnings ("may run out in 40m, before it resets"), session depleted and restored, limit resets (off by default) and sign-in needed. Each one has its own switch in *Settings*.
- **Providers:** Claude (Pro/Max, via your Claude Code login) and Codex (via your Codex CLI login)

## Install

Download the latest release and run the installer:

```bash
curl -LO https://github.com/eduardafa/tokenbar-linux/releases/latest/download/tokenbar-linux-x86_64.tar.gz
tar xzf tokenbar-linux-x86_64.tar.gz && cd tokenbar-linux
./install.sh
```

Then add **TokenBar** in *Settings → Desktop → Panel → Configure panel applets*.

Everything installs per user into `~/.local`, with no root. `./install.sh --uninstall` removes it.

See [Where it runs](#where-it-runs) for your distro and desktop.

<details>
<summary>Build from source</summary>

```bash
git clone https://github.com/eduardafa/tokenbar-linux.git && cd tokenbar-linux
./install.sh   # builds the applet if Rust is installed (rustup.rs)
```

Building needs the `xkbcommon` headers. On Debian, Ubuntu and Pop!_OS, `install.sh` fetches them without sudo.
</details>

## Where it runs

TokenBar has four parts with different requirements:

| | Panel applet | Tray icon | Dashboard window | CLI |
|---|:-:|:-:|:-:|:-:|
| **COSMIC**: Pop!_OS 24.04+, Fedora COSMIC, Arch | ✅ | ✅ | ✅ | ✅ |
| **KDE Plasma** | | ✅ | ✅ | ✅ |
| **XFCE, Cinnamon, MATE, Budgie** | | ✅ | ✅ | ✅ |
| **GNOME** (Ubuntu, Fedora Workstation) | | ✅ with the [AppIndicator extension](https://extensions.gnome.org/extension/615/appindicator-support/) | ✅ | ✅ |
| **Older distros**: Debian 12, Ubuntu 22.04 | | | | ✅ |
| **macOS** | | | | ⚠️ cost only, use [CodexBar](https://codexbar.app) |
| **Windows** | | | | ⚠️ untested |

What each part needs:

- **CLI:** Python 3.10+. No other packages.
- **Dashboard and tray icon:** GTK 4, libadwaita 1.5+, PyGObject and pycairo. That means Ubuntu 24.04+, Fedora 40+, Debian 13+, Arch or openSUSE Tumbleweed. `install.sh` prints the install command for your package manager if anything is missing. The tray icon works on Wayland and X11.
- **Panel applet:** COSMIC. The prebuilt release binary is x86_64 and needs glibc 2.39+ and `libxkbcommon`, which all current COSMIC distros have. On other CPUs (e.g. ARM), build from source.
- **On macOS** the Claude limits don't load, because Claude Code keeps its login in the Keychain there. On Linux and probably Windows it keeps the login in a file.

Tested on Pop!_OS 24.04 with COSMIC. The other rows follow from the requirements; reports are welcome.

Without COSMIC, install the tray icon instead of the applet:

```bash
TOKENBAR_NO_APPLET=1 ./install.sh --autostart   # tray icon at login
tokenbar usage                                  # terminal output (--json for scripts)
```

## Good to know

- **Cost is an estimate:** list-price value of the tokens in Claude Code's local logs on this machine, not your bill.
- **Adaptive refresh, like CodexBar:** every 2 min after you open the popup, then 5, 15 and finally 30 min as you stay away. While Claude Code or Codex is active (their local logs change), it refreshes at least every 5 min. Opening the popup or pressing Refresh fetches immediately. Responses are cached for 2 minutes and shared across panels, and on a rate limit TokenBar backs off and keeps the last numbers. Turn background refresh off in *Settings* to fetch only on click.
- **Credentials are read-only.** TokenBar reuses existing logins, never refreshes the Claude token, and only talks to the provider APIs and status pages.
- **"Login expired":** run `claude` once to refresh the token.
- **Settings:** *Settings…* in the popup, or `~/.config/tokenbar/config.json`.

## Development

```bash
python3 -m tokenbar usage                  # run from the repo
TOKENBAR_DEMO=1 python3 -m tokenbar        # demo data
scripts/release.sh                         # build a release tarball into dist/
```

`tokenbar/` is the Python backend, CLI and dashboard. `applet/` is the Rust COSMIC applet, which reads `tokenbar usage --json`. To add a provider, add a module in `tokenbar/providers/`.

## License

MIT. Inspired by [CodexBar](https://github.com/steipete/CodexBar) by Peter Steinberger; no code shared.
