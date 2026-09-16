#!/bin/bash
# ============================================================
# sync_db_to_baidu.sh - 数据库文件同步到百度网盘
# ============================================================
#
# 特点:
#   - 百度网盘免费 5GB,需会员才够装 21GB
#   - 免费版上传限速 100-500 KB/s
#   - 每天增量只 10-35 MB,1-3 分钟能传完
#   - 适合"每天上传当天增量快照"
#
# 策略:
#   - 用 rsync 算法,只传变更
#   - 每天传一份压缩备份(节省空间 + 时间)
#   - 保留最近 7 天 / 4 周 / 12 月 快照
#
# 用法:
#   ./sync_db_to_baidu.sh setup       配置 rclone
#   ./sync_db_to_baidu.sh snapshot   创建今天快照并上传
#   ./sync_db_to_baidu.sh restore 2026-09-15  恢复某天快照
#   ./sync_db_to_baidu.sh list       列快照
#   ./sync_db_to_baidu.sh cleanup    删除过期快照

set -e

# ============================================================
# 配置
# ============================================================
RCLONE_BIN="${RCLONE_BIN:-rclone}"
REMOTE_NAME="${REMOTE_NAME:-baidu-tradingagent}"
REMOTE_PATH="${REMOTE_PATH:-/TradingAgent-data/snapshots}"
LOCAL_DATA_DIR="$HOME/TradingAgent/offlineDataManager/data"
SNAPSHOT_DIR="$HOME/.tradingagent-snapshots"
RETENTION_DAILY=7
RETENTION_WEEKLY=4
RETENTION_MONTHLY=12

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

check_rclone() {
    if ! command -v "$RCLONE_BIN" >/dev/null 2>&1; then
        log_error "rclone not installed: $RCLONE_BIN"
        echo "Install: brew install rclone"
        exit 1
    fi
}

cmd_setup() {
    log_step "百度网盘 rclone 配置"
    echo ""
    echo "步骤:"
    echo "1. brew install rclone"
    echo "2. rclone config"
    echo "   > n  (New remote)"
    echo "   > name> baidu-tradingagent"
    echo "   > Storage> baidu"
    echo "   > client_id> (Enter, 用 rclone 默认)"
    echo "   > client_secret> (Enter)"
    echo "   > y (Yes)"
    echo "3. 浏览器会弹窗,登录你的百度账号并授权"
    echo "4. 测试: rclone lsd $REMOTE_NAME:"
    echo ""
    log_info "完成后跑: ./sync_db_to_baidu.sh snapshot"
}

cmd_snapshot() {
    check_rclone
    log_step "创建并上传今天快照"

    local date_str=$(date +%Y-%m-%d)
    local snapshot_name="snapshot_${date_str}.tar.gz"
    local snapshot_path="$SNAPSHOT_DIR/$snapshot_name"

    # 1. 创建快照目录
    mkdir -p "$SNAPSHOT_DIR"

    # 2. 检查今天是否已存在
    if [ -f "$snapshot_path" ]; then
        log_warn "今天快照已存在: $snapshot_path"
        log_info "  跳过压缩,直接上传"
    else
        # 3. 压缩
        log_info "压缩数据库到 $snapshot_path ..."
        tar czf "$snapshot_path" -C "$LOCAL_DATA_DIR" \
            --exclude='*.db-journal' \
            --exclude='*.db-wal' \
            --exclude='*.db-shm' \
            $(ls "$LOCAL_DATA_DIR" | grep '\.db$')

        local size=$(du -h "$snapshot_path" | cut -f1)
        log_info "  压缩完成: $size"
    fi

    # 4. 上传到百度网盘
    log_info "上传到 $REMOTE_NAME:$REMOTE_PATH ..."
    "$RCLONE_BIN" copy "$snapshot_path" "$REMOTE_NAME:$REMOTE_PATH" \
        --progress \
        --transfers=2 \
        --checkers=4 \
        --retries=3

    log_info "上传完成"
    log_info "云端路径: $REMOTE_NAME:$REMOTE_PATH/$snapshot_name"
}

cmd_restore() {
    check_rclone

    local date_str="${1:-}"
    if [ -z "$date_str" ]; then
        log_error "用法: $0 restore YYYY-MM-DD"
        exit 1
    fi

    local snapshot_name="snapshot_${date_str}.tar.gz"

    log_step "恢复快照: $snapshot_name"

    # 检查云端
    if ! "$RCLONE_BIN" stat "$REMOTE_NAME:$REMOTE_PATH/$snapshot_name" >/dev/null 2>&1; then
        log_error "云端不存在: $snapshot_name"
        log_info "可用快照:"
        cmd_list
        exit 1
    fi

    # 下载到临时目录
    local tmp_dir=$(mktemp -d)
    log_info "下载到: $tmp_dir"
    "$RCLONE_BIN" copy "$REMOTE_NAME:$REMOTE_PATH/$snapshot_name" "$tmp_dir/" --progress

    # 解压
    log_warn "解压会覆盖当前 DB,继续? (y/n)"
    read -r confirm
    if [ "$confirm" != "y" ]; then
        log_info "已取消"
        rm -rf "$tmp_dir"
        exit 0
    fi

    tar xzf "$tmp_dir/$snapshot_name" -C "$LOCAL_DATA_DIR"
    rm -rf "$tmp_dir"

    log_info "恢复完成"
    log_warn "注意:需要重启 service 才能看到新数据"
}

cmd_list() {
    check_rclone

    log_step "云端快照列表: $REMOTE_NAME:$REMOTE_PATH"
    "$RCLONE_BIN" lsf "$REMOTE_NAME:$REMOTE_PATH" 2>&1
}

cmd_cleanup() {
    check_rclone

    log_step "清理过期快照"
    log_info "保留策略: 日 $RETENTION_DAILY 天 / 周 $RETENTION_WEEKLY 周 / 月 $RETENTION_MONTHLY 月"

    # 列云端所有快照
    local snapshots=$("$RCLONE_BIN" lsf "$REMOTE_NAME:$REMOTE_PATH" 2>/dev/null | grep "^snapshot_" | sort)

    if [ -z "$snapshots" ]; then
        log_info "没有快照,跳过"
        return 0
    fi

    local total=$(echo "$snapshots" | wc -l | tr -d ' ')
    log_info "云端现有 $total 个快照"

    # 计算要保留的日期
    local today=$(date +%Y-%m-%d)
    local weekly_dates=""
    for i in $(seq 0 $((RETENTION_WEEKLY-1))); do
        local d=$(date -v -${i}w +%Y-%m-%d 2>/dev/null || date -d "-$i weeks" +%Y-%m-%d 2>/dev/null)
        weekly_dates="$weekly_dates $d"
    done
    local monthly_dates=""
    for i in $(seq 0 $((RETENTION_MONTHLY-1))); do
        local d=$(date -v -${i}m +%Y-%m-%d 2>/dev/null || date -d "-$i months" +%Y-%m-%d 2>/dev/null)
        monthly_dates="$monthly_dates $d"
    done

    # 决定哪些删
    local deleted=0
    echo "$snapshots" | while read snap; do
        local snap_date=$(echo "$snap" | sed 's/snapshot_\(.*\)\.tar\.gz/\1/')
        local keep=0

        # 7 天内保留
        local days_old=$(( ($(date +%s) - $(date -j -f "%Y-%m-%d" "$snap_date" +%s 2>/dev/null || date -d "$snap_date" +%s) ) / 86400 ))
        if [ "$days_old" -lt "$RETENTION_DAILY" ]; then
            keep=1
        fi

        # 周节点保留
        if echo "$weekly_dates" | grep -q "$snap_date"; then
            keep=1
        fi

        # 月节点保留
        if echo "$monthly_dates" | grep -q "$snap_date"; then
            keep=1
        fi

        if [ "$keep" -eq 0 ]; then
            "$RCLONE_BIN" delete "$REMOTE_NAME:$REMOTE_PATH/$snap" 2>&1 | head -1
            deleted=$((deleted + 1))
            echo "  删除: $snap"
        fi
    done

    log_info "清理完成,删除 $deleted 个过期快照"
}

cmd_help() {
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  setup       Configure rclone for Baidu"
    echo "  snapshot    Create + upload today's snapshot"
    echo "  restore YYYY-MM-DD  Restore from snapshot"
    echo "  list        List all snapshots on cloud"
    echo "  cleanup     Delete expired snapshots (daily 7 / weekly 4 / monthly 12)"
    echo "  help        Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 setup"
    echo "  $0 snapshot"
    echo "  $0 restore 2026-09-15"
    echo "  $0 cleanup"
    echo ""
    echo "Notes:"
    echo "  - Baidu free: 5GB, ~100-500KB/s upload"
    echo "  - Member: 1.5TB, ~1MB/s"
    echo "  - SVIP: 5TB, ~10MB/s"
    echo "  - Each daily snapshot ~10-35MB"
}

CMD="${1:-help}"
shift 2>/dev/null || true

case "$CMD" in
    setup)      cmd_setup "$@" ;;
    snapshot)   cmd_snapshot "$@" ;;
    restore)    cmd_restore "$@" ;;
    list)       cmd_list "$@" ;;
    cleanup)    cmd_cleanup "$@" ;;
    help|--help|-h|"") cmd_help ;;
    *)
        log_error "Unknown command: $CMD"
        cmd_help
        exit 1
        ;;
esac