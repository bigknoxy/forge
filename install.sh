#!/usr/bin/env bash
set -euo pipefail

echo "Installing FORGE prerequisites..."

# Update and install packages
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl gnupg2 

# Install gh CLI if missing
if ! command -v gh >/dev/null 2>&1; then
  echo "Installing GitHub CLI..."
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
  apt-get update
  apt-get install -y gh
fi

# Create virtualenv
python3 -m venv /opt/forge-venv
/opt/forge-venv/bin/pip install --upgrade pip
/opt/forge-venv/bin/pip install -r /root/code/forge/requirements.txt

# Install forge CLI
ln -sf /root/code/forge/forge_cli.py /usr/local/bin/forge
chmod +x /usr/local/bin/forge

# Install start-wrapper
mkdir -p /opt/forge/bin
cp /root/code/forge/scripts/forge_start.py /opt/forge/bin/forge-start
chmod +x /opt/forge/bin/forge-start

# Create directories
mkdir -p /workspace/projects /workspace/research /workspace/skills /workspace/logs /data
chown -R $(whoami) /workspace /data || true

# Copy systemd unit
cp /root/code/forge/systemd/forge.service /etc/systemd/system/forge.service
systemctl daemon-reload
systemctl enable --now forge || echo "Systemd start may require root privileges"

cat <<'EOF'

🎉 INSTALL COMPLETE

Next steps (required):
1) Edit /root/code/forge/.env and set OPENROUTER_API_KEY, GH_TOKEN, and model choices.
   - e.g. EDITOR=nano sudo -E nano /root/code/forge/.env
2) Authenticate the GitHub CLI (if applicable):
   - gh auth login
3) Start or check the service (systemd systems):
   - sudo systemctl start forge
   - sudo systemctl status forge

Quick checks (non-systemd):
- You can use the included CLI: /usr/local/bin/forge (aliases: forge)
  - forge start      # tries systemctl, falls back to launch uvicorn and create WORKSPACE_DIR/forge.pid
  - forge stop       # tries systemctl, falls back to kill PID found in pidfile or process list
  - forge pause-app  # APPLICATION-LEVEL pause (recommended): stops agents making outbound calls
  - forge resume-app # clear application maintenance mode

Changelog (recent):

$(sed -n '1,200p' /root/code/forge/CHANGELOG.md 2>/dev/null || echo 'No changelog found')

Post-install notes & recommendations:
- For short-term halting of API usage, use: forge pause-app
  This will stop task dispatch and block model/API calls without killing the process.
- For full shutdown and letting systemd know the service is stopped: sudo systemctl stop forge
- The CLI writes audit entries to the DB logs table for most control actions so you can review operator actions later.

If you need to troubleshoot:
- Tail logs: tail -F /workspace/forge.out.log /workspace/forge.err.log
- View agent logs: /workspace/logs/<agent>.log

Thanks for installing FORGE! 🚀

EOF
