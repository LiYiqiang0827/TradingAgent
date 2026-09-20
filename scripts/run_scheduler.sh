#!/bin/zsh
set -e

root=/Users/nickzhang/TradingAgent
source "$root/.env"
export PATH="/Users/nickzhang/.local/bin:$root/.venv/bin:$PATH"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"

case "$1" in
  offline)
    cd "$root/offlineDataManager/scripts"
    exec "$root/.venv/bin/python" -m scheduler.scheduler_updateData --daemon
    ;;
  online)
    cd "$root/onlineDataManager/scripts"
    exec "$root/.venv/bin/python" -m scheduler.scheduler_onlineData --daemon
    ;;
  *)
    print -u2 'usage: run_scheduler.sh offline|online'
    exit 2
    ;;
esac
