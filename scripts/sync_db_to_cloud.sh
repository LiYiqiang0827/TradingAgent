#!/bin/bash
# ============================================================
# sync_db_to_cloud.sh - 数据库文件云端同步
# ============================================================
#
# 用途:
#   把 ~/TradingAgent/{offline,online}DataManager/data/ 的 SQLite
#   增量同步到云端(OSS / S3 / Google Drive 等)。
#
# 推荐工具:
#   - rclone(支持 OSS/S3/GDrive/Dropbox 等,免费开源)
#   - 安装:brew install rclone
#   - 配置:rclone config (创建 remote,例 'oss-cn-hangzhou')
#
# 用法:
#   ./sync_db_to_cloud.sh status    看本地 vs 远云端 差异
#   ./sync_db_to_cloud.sh sync      增量上传到云
#   ./sync_db_to_cloud.sh pull      从云端拉取(异地机器恢复时用)
#   ./sync_db_to_cloud.sh list      列出远云端 文件
#   ./sync_db_to_cloud.sh setup     配置向导(引导设 rclone remote)
#
# ============================================================

set -e

# ============================================================
# 配置
# ============================================================
RCLONE_BIN="${RCLONE_BIN:-rclone}"
REMOTE_NAME="${REMOTE_NAME:-oss-tradingagent}"
REMOTE_PATH="${REMOTE_PATH:-tradingagent-data}"

LOCAL_DATA_DIRS=(
    "$HOME/TradingAgent/offlineDataManager/data"
    "$HOME/TradingAgent/onlineDataManager/data"
    "$HOME/TradingAgent/policyStudy/data"
)

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# ============================================================
# 工具函数
# ============================================================

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $1"; }

check_rclone() {
    if ! command -v "$RCLONE_BIN" >/dev/null 2>&1; then
        log_error "rclone 未安装: $RCLONE_BIN"
        echo ""
        echo "安装方法:"
        echo "  macOS:  brew install rclone"
        echo "  Linux:  curl https://rclone.org/install.sh | sudo bash"
        echo "  Windows: https://rclone.org/install/"
        exit 1
    fi
}

list_all_local_db() {
    for d in "${LOCAL_DATA_DIRS[@]}"; do
        if [ -d "$d" ]; then
            find "$d" -name "*.db" -type f 2>/dev/null
        fi
    done
}

get_local_size() {
    du -ch $(list_all_local_db 2>/dev/null) 2>/dev/null | tail -1 | awk '{print $1}'
}

cmd_setup() {
    log_step "rclone 配置向导"
    echo ""
    echo "如果你已经配置过 rclone,跳过这步。"
    echo ""
    echo "step 1: 安装 rclone (brew install rclone)"
    echo "step 2: 运行 rclone config 配一个 remote (例 oss-tradingagent)"
    echo "step 3: 测试: rclone lsd $REMOTE_NAME:"
    echo "step 4: 重新跑这个脚本"
    echo ""
    echo "详细配置示例(以阿里云 OSS 为例):"
    echo ""
    cat <<'EOF'
n) New remote
name> oss-tradingagent
Storage> s3
provider> Alibaba
access_key_id> YOUR_ACCESS_KEY_ID
secret_access_key> YOUR_SECRET_ACCESS_KEY
endpoint> oss-cn-hangzhou.aliyuncs.com
y) Yes this is OK
EOF
    echo ""
    log_info "设置 REMOTE_NAME 环境变量告诉脚本用哪个 remote:"
    echo "  export REMOTE_NAME=oss-tradingagent"
}

cmd_status() {
    check_rclone

    log_step "本地 vs 远云端 状态"
    echo ""

    # 列出所有本地 DB
    local local_dbs=$(list_all_local_db)
    if [ -z "$local_dbs" ]; then
        log_error "本地找不到任何 .db 文件"
        exit 1
    fi

    log_info "本地 DB 文件:"
    echo "$local_dbs" | while read f; do
        if [ -f "$f" ]; then
            sz=$(du -h "$f" | cut -f1)
            md5=$(md5sum "$f" | cut -d' ' -f1 | cut -c1-12)
            rel=$(echo "$f" | sed "s|$HOME/||")
            echo "  [LOCAL] $rel ($sz, md5=$md5)"
        fi
    done
    echo ""

    # 列出远云端
    log_info "远云端 DB 文件 ($REMOTE_NAME:$REMOTE_PATH):"
    if "$RCLONE_BIN" lsf "$REMOTE_NAME:$REMOTE_PATH" --recursive 2>/dev/null; then
        "$RCLONE_BIN" lsf "$REMOTE_NAME:$REMOTE_PATH" --recursive 2>/dev/null | while read f; do
            echo "  [REMOTE] $f"
        done
    else
        log_warn "  无法列出(检查 rclone config)"
    fi

    echo ""
    log_info "本地总大小: $(get_local_size)"

    # 远程总大小
    local remote_size=$("$RCLONE_BIN" size "$REMOTE_NAME:$REMOTE_PATH" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('bytes',0)/1024/1024/1024)" 2>/dev/null || echo "unknown")
    log_info "远云端总大小: ${remote_size} GB"
}

cmd_sync() {
    check_rclone

    log_step "增量同步到云端: $REMOTE_NAME:$REMOTE_PATH"
    echo ""

    local total_uploaded=0
    local total_files=0

    for d in "${LOCAL_DATA_DIRS[@]}"; do
        if [ ! -d "$d" ]; then
            log_warn "跳过(不存在): $d"
            continue
        fi
        log_info "同步目录: $d"

        # rclone sync - 上传差异文件,删除远端多余(可选)
        # --update: 仅当本地文件更新才上传
        # --transfers=4: 并发传输
        # --checkers=8: 并发检查
        # --stats=10s: 每10秒打印进度
        # --bwlimit=0: 不限速

        "$RCLONE_BIN" sync "$d" "$REMOTE_NAME:$REMOTE_PATH/$(basename $d)" \
            --update \
            --transfers=4 \
            --checkers=8 \
            --stats=10s \
            --stats-one-line \
            --log-file=/tmp/rclone_sync.log

        local exit_code=$?
        if [ $exit_code -eq 0 ]; then
            log_info "  OK"
        else
            log_error "  失败 (rc=$exit_code)"
            log_warn "  查看日志: /tmp/rclone_sync.log"
        fi
    done

    log_info "同步完成"
}

cmd_pull() {
    check_rclone

    log_step "从云端拉取到本地"
    echo ""
    log_warn "这会覆盖本地同名文件(谨慎使用)"

    local d="$HOME/TradingAgent/offlineDataManager/data"
    if [ ! -d "$(dirname $d)" ]; then
        log_error "父目录不存在,无法恢复"
        exit 1
    fi

    "$RCLONE_BIN" sync "$REMOTE_NAME:$REMOTE_PATH/offlineDataManager/data" "$d" \
        --update \
        --transfers=4 \
        --checkers=8 \
        --stats=10s \
        --stats-one-line \
        --log-file=/tmp/rclone_pull.log

    log_info "恢复完成"
}

cmd_list() {
    check_rclone

    log_step "远云端文件列表: $REMOTE_NAME:$REMOTE_PATH"
    "$RCLONE_BIN" ls "$REMOTE_NAME:$REMOTE_PATH" --recursive
    echo ""
    "$RCLONE_BIN" size "$REMOTE_NAME:$REMOTE_PATH"
}

cmd_help() {
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  setup   引导配置 rclone remote"
    echo "  status  对比本地和远云端"
    echo "  sync    增量同步本地到云端"
    echo "  pull    从云端拉取(异地恢复用)"
    echo "  list    列出云端所有文件"
    echo "  help    显示本帮助"
    echo ""
    echo "前置:"
    echo "  brew install rclone"
    echo "  rclone config  # 创建一个 remote,例 oss-tradingagent"
    echo ""
    echo "环境变量(可选):"
    echo "  REMOTE_NAME    rclone config 名(默认 oss-tradingagent)"
    echo "  REMOTE_PATH    远端路径(默认 tradingagent-data)"
    echo ""
    echo "示例:"
    echo "  $0 setup"
    echo "  REMOTE_NAME=oss-tradingagent $0 sync"
    echo "  $0 status"
}

CMD="${1:-help}"
shift 2>/dev/null || true

case "$CMD" in
    setup)  cmd_setup "$@" ;;
    status) cmd_status "$@" ;;
    sync)   cmd_sync "$@" ;;
    pull)   cmd_pull "$@" ;;
    list)   cmd_list "$@" ;;
    help|--help|-h|"") cmd_help ;;
    *)
        log_error "Unknown command: $CMD"
        cmd_help
        exit 1
        ;;
esac