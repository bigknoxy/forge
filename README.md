# FORGE - Fully Orchestrated Reasoning and Generation Engine 🚀

A multi-agent orchestration platform that runs as a long-lived daemon and provides a lightweight web UI and CLI to manage autonomous agents. FORGE is designed to be operable both inside a systemd-managed VM/LXC and in containerized or non-systemd environments.

Quick highlights
- App-level maintenance mode to safely pause outbound API/LLM calls without killing processes ⚖️
- Portable CLI that prefers systemd but falls back to pidfile-based process control for non-systemd systems 🛠️
- Start-wrapper that writes a PIDFile so system managers and CLI agree on process identity 🔗
- Audit logging of CLI operations into the DB for operator traceability 🧾

Table of contents
- Quick start
- Operational controls (system-level and app-level)
- Files and locations
- Troubleshooting
- Uninstall
- Changelog

Quick start (Debian/LXC)
1. Copy `.env` from `.env.example` and populate credentials and models (OPENROUTER_API_KEY, GH_TOKEN, HENRY_MODEL, etc.):
   cp .env.example .env && edit .env
2. One-line install (recommended):
   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/bigknoxy/forge/main/install.sh)"
3. Or run the installer locally:
   sudo ./install.sh
4. Verify the service (systemd):
   sudo systemctl status forge
5. Use the CLI for quick checks:
   forge status
   forge logs

If you're running in a container or Alpine without systemd, use the CLI start/stop that falls back to spawning uvicorn and managing a pidfile.

Operational controls

There are two control layers: system-level (systemd/process manager) and application-level (maintenance mode).

System-level (recommended when systemd is used)
- sudo systemctl start forge    — start the service
- sudo systemctl stop forge     — stop the service (preferred vs killing the process)
- sudo systemctl status forge   — check service status

CLI (portable) — commands
- forge start                   — prefer systemctl start; fallback spawns uvicorn and writes WORKSPACE_DIR/forge.pid
- forge stop [--force]          — prefer systemctl stop; fallback SIGTERM to PIDs, --force issues SIGKILL
- forge pause                   — OS-level SIGSTOP (last-resort)
- forge resume                  — OS-level SIGCONT

Application-level maintenance (recommended for pausing API calls)
- forge pause-app               — set maintenance flag in DB; agents stop taking new work and model calls are blocked
- forge resume-app              — clear the maintenance flag
- forge maintenance-status      — show maintenance flag value

Why prefer app-level pause-app? 🌟
- It immediately prevents new outbound LLM/API traffic while preserving in-memory state.
- The event loop and agents check the maintenance flag and avoid new work.
- Safer than SIGSTOP: no surprising interactions with supervisors/watchdogs and you get clear audit logs.

Files and locations
- Source: /root/code/forge
- Runtime:
  - Workspace: /workspace (projects, logs, etc.)
  - Data/DB: /data/forge.db
  - Fallback DB location: If /data is not writable, FORGE will fall back to a safe temp location. You can override the fallback using the FORGE_FALLBACK_DATA_DIR environment variable before starting the app.
- CLI: /usr/local/bin/forge (symlink to forge_cli.py)
- Start wrapper (systemd): /opt/forge/bin/forge-start
- Systemd unit (if installed): /etc/systemd/system/forge.service
- PID file (wrapper/non-systemd): /var/run/forge/forge.pid (FORGE_RUNTIME_DIR)
- CLI fallback pidfile (when starting without systemd): WORKSPACE_DIR/forge.pid (default /workspace/forge.pid)

Troubleshooting
- Tail logs: tail -F /workspace/forge.out.log /workspace/forge.err.log
- Agent logs: /workspace/logs/<agent>.log
- Check DB: sqlite3 /data/forge.db
- If systemctl is present but you see unexpected behavior, try using `sudo systemctl stop forge` to ensure systemd is aware of your intent.

Uninstall

If you need to remove FORGE from a host, the CLI provides a safe, configurable uninstall flow. The uninstall command stops running services first and then optionally deletes data, the virtualenv, and the systemd unit.

Usage examples:
- Preview removal without making changes (dry-run):
  .venv/bin/python3 forge_cli.py uninstall --dry-run --purge-data --remove-venv --remove-systemd

- Interactive uninstall (will prompt for confirmation):
  .venv/bin/python3 forge_cli.py uninstall --purge-data

- Non-interactive uninstall (will remove data, venv, and systemd unit):
  sudo .venv/bin/python3 forge_cli.py uninstall --purge-data --remove-venv --remove-systemd --yes

What the command does:
- Stops the service (prefers systemctl stop; falls back to killing repo-owned uvicorn processes).
- If --purge-data is passed, the command backs up the DB to /tmp and removes DATA_DIR (default /data) and workspace logs.
- If --remove-venv is passed, it removes the virtualenv at /opt/forge-venv (requires root if under /opt).
- If --remove-systemd is passed, it disables and removes /etc/systemd/system/forge.service (requires root).
- By default the uninstall prompts and supports --dry-run and --yes for scripting.

Safety notes
- The uninstall makes a DB backup to /tmp before deleting the DB when --purge-data is used. If you want the backup elsewhere, move it from /tmp after uninstall.
- Removing systemd units or /opt directories generally requires root; run the command with sudo for those actions.
- The uninstall leaves repository files in-place (it does not delete files inside the cloned repo directory), but it will remove runtime data in /data and /workspace if requested.

Changelog (recent)
- See CHANGELOG.md in the repo for release notes and details.

Thanks for using FORGE — if you'd like, I can add an authenticated admin HTTP endpoint to toggle maintenance mode remotely, or prepare a release tarball. ✨
