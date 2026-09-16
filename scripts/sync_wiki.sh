#!/bin/bash
# ============================================================
# sync_wiki.sh - LLM Wiki/TradingAgent/ <-> GitHub wiki-TradingAgent
# ============================================================
#
# LLM Wiki/TradingAgent/ IS the local git repo (no separate ~/wiki-TradingAgent/)
# 这个脚本封装常用的 git 操作,方便不熟悉 git 的用户使用
#
# Usage:
#   ./sync_wiki.sh push       检测 LLM Wiki 改动 → git add → commit → push
#   ./sync_wiki.sh pull       从 GitHub 拉取最新 → 同步到 LLM Wiki 文件
#   ./sync_wiki.sh status     看本地仓库 + 远程 状态
#   ./sync_wiki.sh commit "msg"  自定义 commit 信息
#   ./sync_wiki.sh log        看最近 10 个 commit
#   ./sync_wiki.sh help       显示帮助

set -e

# ============================================================
# Config
# ============================================================
LLM_WIKI_DIR="$HOME/LLM Wiki/TradingAgent"
REMOTE="origin"
BRANCH="main"
COMMIT_PREFIX="docs:"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

# ============================================================
# Prerequisite check
# ============================================================

check_prereq() {
    if [ ! -d "$LLM_WIKI_DIR" ]; then
        log_error "LLM Wiki dir not found: $LLM_WIKI_DIR"
        exit 1
    fi

    if [ ! -d "$LLM_WIKI_DIR/.git" ]; then
        log_error "LLM Wiki/TradingAgent/ is not a git repo!"
        log_error "Please run: cd \"$LLM_WIKI_DIR\" && git init && git remote add origin git@github.com:LiYiqiang0827/wiki-TradingAgent.git"
        exit 1
    fi

    cd "$LLM_WIKI_DIR"
}

# ============================================================
# Commands
# ============================================================

cmd_push() {
    check_prereq

    log_step "[push] Step 1/4: check uncommitted changes..."
    local uncommitted=$(git status --porcelain | wc -l | tr -d ' ')
    if [ "$uncommitted" -eq 0 ]; then
        log_info "  No local changes to commit"
        log_info "  (Use 'git status' to verify nothing pending)"
        return 0
    fi

    log_info "  Found $uncommitted changed files:"
    git status --short | head -20
    if [ "$uncommitted" -gt 20 ]; then
        log_info "  ... and $((uncommitted - 20)) more"
    fi

    log_step "[push] Step 2/4: git pull --rebase(避免冲突)..."
    if ! git pull --rebase "$REMOTE" "$BRANCH" 2>&1 | tail -5; then
        log_warn "Pull failed (maybe no upstream or conflicts)"
        log_warn "Continue anyway (may fail at push)"
    fi

    log_step "[push] Step 3/4: git add + commit..."
    local msg="${1:-${COMMIT_PREFIX} update docs $(date +%Y-%m-%d %H:%M:%S)}"
    git add .
    if git diff --cached --quiet; then
        log_info "  Nothing to commit (all changes already committed)"
        return 0
    fi
    git commit -m "$msg"

    log_step "[push] Step 4/4: git push..."
    if git push "$REMOTE" "$BRANCH"; then
        log_info "Push done: $msg"
    else
        log_error "Push failed! Resolve conflicts and try again."
        exit 1
    fi
}

cmd_pull() {
    check_prereq

    log_step "[pull] Step 1/2: check local changes..."
    local uncommitted=$(git status --porcelain | wc -l | tr -d ' ')
    if [ "$uncommitted" -gt 0 ]; then
        log_warn "  You have $uncommitted uncommitted local changes"
        log_warn "  They will be stashed temporarily"
        git stash push -u -m "sync_wiki auto stash $(date +%Y-%m-%d_%H:%M:%S)"
        local stashed=1
    else
        local stashed=0
    fi

    log_step "[pull] Step 2/2: git pull --rebase..."
    if git pull --rebase "$REMOTE" "$BRANCH"; then
        log_info "Pull done"
        if [ "$stashed" -eq 1 ]; then
            log_info "Restoring stashed changes..."
            git stash pop || log_warn "Stash pop failed, manual review needed"
        fi
    else
        log_error "Pull failed (likely merge conflict)"
        log_error "Resolve manually, then: cd \"$LLM_WIKI_DIR\" && git status"
        exit 1
    fi
}

cmd_commit() {
    check_prereq
    local msg="${1:?Usage: $0 commit \"your commit message\"}"
    git add .
    git diff --cached --quiet && log_info "Nothing to commit" && return 0
    git commit -m "$msg"
    log_info "Committed: $msg"
    log_info "Run '$0 push' to push to GitHub"
}

cmd_status() {
    check_prereq

    echo "==================================="
    echo "  Wiki repo status"
    echo "==================================="

    echo ""
    echo "Repo:     $LLM_WIKI_DIR (also git repo)"
    echo "Remote:   git@github.com:LiYiqiang0827/wiki-TradingAgent.git"
    echo "Branch:   $BRANCH"
    echo ""

    echo "--- git status (uncommitted) ---"
    git status --short
    local n=$(git status --porcelain | wc -l | tr -d ' ')
    echo "  Total: $n file(s)"

    echo ""
    echo "--- branch vs remote ---"
    git fetch "$REMOTE" 2>&1 | tail -1
    local ahead=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)
    local behind=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)
    if [ "$ahead" -eq 0 ] && [ "$behind" -eq 0 ]; then
        echo "  In sync with remote"
    else
        [ "$ahead" -gt 0 ] && echo "  Local ahead by $ahead commit(s)"
        [ "$behind" -gt 0 ] && echo "  Local behind by $behind commit(s)"
    fi

    echo ""
    echo "--- recent 5 commits ---"
    git log --oneline -5
}

cmd_log() {
    check_prereq
    git log --oneline -10
}

cmd_help() {
    echo "Usage: $0 <command>"
    echo ""
    echo "LLM Wiki/TradingAgent/ is itself a git repo synced with GitHub."
    echo ""
    echo "Commands:"
    echo "  push               Add + commit + push all local changes"
    echo "  pull               Pull remote changes into LLM Wiki"
    echo "  commit \"msg\"       Commit staged changes with custom msg"
    echo "  status             Show git status + remote sync status"
    echo "  log                Show recent commits"
    echo "  help               Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 push                    default commit msg (timestamped)"
    echo "  $0 push \"fix typos in 03_scheduler\""
    echo "  $0 commit \"add new section\""
    echo ""
    echo "Workflow:"
    echo "  1. Edit .md files in ~/LLM Wiki/TradingAgent/"
    echo "  2. Run: $0 push"
    echo "  3. Done! GitHub synced"
}

CMD="${1:-help}"
shift 2>/dev/null || true

case "$CMD" in
    push)              cmd_push "$@" ;;
    pull)              cmd_pull "$@" ;;
    commit)            cmd_commit "$@" ;;
    status)            cmd_status "$@" ;;
    log)               cmd_log "$@" ;;
    help|--help|-h|"") cmd_help ;;
    *)
        log_error "Unknown command: $CMD"
        cmd_help
        exit 1
        ;;
esac