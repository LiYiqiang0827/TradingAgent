#!/bin/bash
# ============================================================
# sync_wiki.sh - LLM Wiki to wiki-TradingAgent sync
# ============================================================
#
# Purpose:
#   1. Sync ~/LLM Wiki/TradingAgent/ content to ~/wiki-TradingAgent/
#   2. Auto commit + push to GitHub
#
# Usage:
#   ./sync_wiki.sh push       - LLM Wiki to local repo to commit to push
#   ./sync_wiki.sh pull       - GitHub to local repo to copy to LLM Wiki
#   ./sync_wiki.sh status     - Show differences
#   ./sync_wiki.sh init       - First-time init local wiki repo

set -e

# Config
LLM_WIKI_DIR="$HOME/LLM Wiki/TradingAgent"
WIKI_REPO_DIR="$HOME/wiki-TradingAgent"
REMOTE="origin"
BRANCH="main"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_prereq() {
    if [ ! -d "$LLM_WIKI_DIR" ]; then
        log_error "LLM Wiki dir not found: $LLM_WIKI_DIR"
        exit 1
    fi
    if [ ! -d "$WIKI_REPO_DIR" ]; then
        log_warn "Wiki repo dir not found: $WIKI_REPO_DIR"
        log_warn "First run: $0 init"
        exit 1
    fi
}

copy_files() {
    # Sync LLM Wiki to wiki repo (exclude junk)
    # CRITICAL: never delete .git directory or .gitignore
    local src="$1"
    local dst="$2"

    log_info "Syncing files: $src -> $dst"

    rsync -av --delete \
        --exclude='.DS_Store' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.swp' \
        --exclude='*.tmp' \
        --filter='protect .git/' \
        --filter='protect .gitignore' \
        "$src/" "$dst/"

    local n_files=$(find "$dst" -type f -not -path "./.git/*" | wc -l | tr -d ' ')
    log_info "  $n_files files"
}

cmd_init() {
    if [ -d "$WIKI_REPO_DIR/.git" ]; then
        log_warn "Wiki repo already exists: $WIKI_REPO_DIR"
        return 0
    fi

    log_info "Initializing wiki repo..."
    mkdir -p "$WIKI_REPO_DIR"
    cd "$WIKI_REPO_DIR"
    git init
    git remote add "$REMOTE" "git@github.com:LiYiqiang0827/wiki-TradingAgent.git"

    copy_files "$LLM_WIKI_DIR" "$WIKI_REPO_DIR"

    git add .
    git commit -m "init: LLM Wiki doc repo auto init"
    log_info "Init done"
    log_info "Next: create empty wiki-TradingAgent repo on GitHub, then run: $0 push"
}

cmd_push() {
    check_prereq
    cd "$WIKI_REPO_DIR"

    log_info "[push] Step 1/4: sync from LLM Wiki..."
    copy_files "$LLM_WIKI_DIR" "$WIKI_REPO_DIR"

    log_info "[push] Step 2/4: check git status..."
    local has_changes=$(git status --porcelain | wc -l | tr -d ' ')
    if [ "$has_changes" -eq 0 ]; then
        log_info "  No changes, skip push"
        return 0
    fi

    git status --short

    log_info "[push] Step 3/4: commit..."
    local msg="${1:-update: LLM Wiki sync $(date +%Y-%m-%d)}"
    git add .
    git commit -m "$msg"

    log_info "[push] Step 4/4: push to GitHub..."
    git push "$REMOTE" "$BRANCH"

    log_info "Push done"
}

cmd_pull() {
    check_prereq
    cd "$WIKI_REPO_DIR"

    log_info "[pull] Step 1/3: pull from GitHub..."
    git pull "$REMOTE" "$BRANCH"

    log_info "[pull] Step 2/3: copy to LLM Wiki..."
    copy_files "$WIKI_REPO_DIR" "$LLM_WIKI_DIR"

    log_info "[pull] Step 3/3: check LLM Wiki changes..."
    log_info "Pull done"
    log_warn "Note: LLM Wiki is edit-only, version control is in this repo"
}

cmd_status() {
    check_prereq
    cd "$WIKI_REPO_DIR"

    echo "==================================="
    echo "  Wiki repo status"
    echo "==================================="

    echo ""
    echo "Local repo: $WIKI_REPO_DIR"
    echo "LLM Wiki:   $LLM_WIKI_DIR"
    echo "Remote:     git@github.com:LiYiqiang0827/wiki-TradingAgent.git"
    echo ""

    echo "--- git status ---"
    git status --short
    local n_git_changes=$(git status --porcelain | wc -l | tr -d ' ')
    echo "  Uncommitted: $n_git_changes"

    echo ""
    echo "--- LLM Wiki vs local repo ---"
    rsync -avn --delete --exclude='.DS_Store' "$LLM_WIKI_DIR/" "$WIKI_REPO_DIR/" 2>&1 | tail -5

    echo ""
    echo "--- vs remote origin/main ---"
    git fetch "$REMOTE" 2>&1 | tail -2
    local n_remote=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)
    local n_local=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)
    echo "  Local ahead: $n_remote commits"
    echo "  Remote ahead: $n_local commits"
}

cmd_help() {
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  init    First-time init local wiki repo"
    echo "  push    LLM Wiki to local to commit to push"
    echo "  pull    GitHub to local to copy to LLM Wiki"
    echo "  status  Show differences"
    echo "  help    Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 push                    default commit msg"
    echo "  $0 push 'fix update README'  custom commit msg"
    echo "  $0 pull                    pull from GitHub"
}

CMD="${1:-help}"
shift 2>/dev/null || true

case "$CMD" in
    init)   cmd_init "$@" ;;
    push)   cmd_push "$@" ;;
    pull)   cmd_pull "$@" ;;
    status) cmd_status "$@" ;;
    help|--help|-h|"") cmd_help ;;
    *)
        log_error "Unknown command: $CMD"
        cmd_help
        exit 1
        ;;
esac