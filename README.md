# TokenBar

AI coding plan limits and spend in your Linux panel. A [CodexBar](https://codexbar.app) for Linux.

![TokenBar popup in the COSMIC panel](docs/hero.png)

<sub>Screenshots use demo data.</sub>

## What it shows

- **Panel:** session and weekly % used (`38% · 22%`)
- **Popup:** limits with reset countdowns and pace, spend today and over 30 days, and daily cost by model
- **Providers:** Claude (Pro/Max, via your Claude Code login) and Codex (via your Codex CLI login)

## Install

Download the latest release and run the installer:

```bash
curl -LO https://github.com/eduardafa/tokenbar-linux/releases/latest/download/tokenbar-linux-v0.1.0-x86_64.tar.gz
tar xzf tokenbar-linux-v0.1.0-x86_64.tar.gz && cd tokenbar-linux-v0.1.0-x86_64
./install.sh
```

Then add **TokenBar** in *Settings → Desktop → Panel → Configure panel applets*.

Everything installs per user into `~/.local`, with no root. `./install.sh --uninstall` removes it.

**Requirements:** x86_64, Python 3.10+, GTK 4 and libadwaita 1.5+ (Ubuntu 24.04+, Fedora 40+, Arch). The panel applet needs COSMIC.

<details>
<summary>Build from source</summary>

```bash
git clone https://github.com/eduardafa/tokenbar-linux.git && cd tokenbar-linux
./install.sh   # builds the applet if Rust is installed (rustup.rs)
```

Building needs the `xkbcommon` headers. On Debian, Ubuntu and Pop!_OS, `install.sh` fetches them without sudo.
</details>

## Other desktops

The panel applet is COSMIC-only. Elsewhere, use the tray icon (KDE, XFCE, Cinnamon, and GNOME with the AppIndicator extension) or the CLI:

```bash
TOKENBAR_NO_APPLET=1 ./install.sh --autostart   # tray icon at login
tokenbar usage                                  # terminal output (--json for scripts)
```

## Good to know

- **Cost is an estimate:** list-price value of the tokens in Claude Code's local logs on this machine, not your bill.
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
