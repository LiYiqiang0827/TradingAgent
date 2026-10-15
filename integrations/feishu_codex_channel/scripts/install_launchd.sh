#!/usr/bin/env bash
set -euo pipefail

service_dir="$(cd "$(dirname "$0")/.." && pwd)"
label="com.tradingagent.feishu-codex"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/tradingagent-feishu-codex"
python_bin="$service_dir/.venv/bin/python"

if [[ ! -x "$python_bin" ]]; then
  echo "Missing virtual environment: $python_bin" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$python_bin</string>
    <string>-m</string>
    <string>feishu_codex_channel.main</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$service_dir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$HOME</string>
    <key>CODEX_HOME</key>
    <string>$HOME/.codex</string>
    <key>PYTHONPATH</key>
    <string>$service_dir/src</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$log_dir/channel.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/channel.error.log</string>
</dict>
</plist>
EOF

chmod 600 "$plist"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl enable "gui/$(id -u)/$label"
echo "Installed and started $label"
echo "Logs: $log_dir"
