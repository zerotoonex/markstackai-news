#!/usr/bin/env bash
# ============================================================================
# RSS News Viewer - 一键部署脚本
# 基于 Supervisor 实现长期后台运行，支持多平台
# ============================================================================

set -euo pipefail

# ---- 颜色定义 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---- 项目配置 ----
APP_NAME="rss-viewer"
APP_PORT=37378
APP_FILE="rss_viewer.py"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${APP_DIR}/venv"
DATA_DIR="${APP_DIR}/data"
LOG_DIR="${APP_DIR}/logs"
PID_FILE="${APP_DIR}/${APP_NAME}.pid"
SUPERVISOR_CONF_NAME="${APP_NAME}.conf"

# ---- 工具函数 ----
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
header()  { echo -e "\n${CYAN}${BOLD}═══ $* ═══${NC}\n"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "请使用 root 权限运行此脚本"
        echo "  sudo bash $0"
        exit 1
    fi
}

# ---- OS 检测 ----
detect_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS_ID="${ID,,}"
        OS_VERSION="${VERSION_ID:-}"
    elif [[ "$(uname)" == "Darwin" ]]; then
        OS_ID="macos"
        OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo '')"
    else
        OS_ID="unknown"
        OS_VERSION=""
    fi

    case "$OS_ID" in
        ubuntu|debian|linuxmint|pop)
            PKG_MANAGER="apt"
            PKG_UPDATE="apt-get update -qq"
            PKG_INSTALL="apt-get install -y -qq"
            ;;
        centos|rhel|rocky|almalinux|fedora|amzn)
            PKG_MANAGER="yum"
            PKG_UPDATE="true"
            PKG_INSTALL="yum install -y -q"
            if command -v dnf &>/dev/null; then
                PKG_INSTALL="dnf install -y -q"
            fi
            ;;
        alpine)
            PKG_MANAGER="apk"
            PKG_UPDATE="apk update"
            PKG_INSTALL="apk add --no-cache"
            ;;
        macos)
            PKG_MANAGER="brew"
            PKG_UPDATE="true"
            PKG_INSTALL="brew install"
            ;;
        *)
            error "不支持的操作系统: ${OS_ID}"
            exit 1
            ;;
    esac

    info "检测到系统: ${OS_ID} ${OS_VERSION} (包管理器: ${PKG_MANAGER})"
}

# ---- 依赖安装 ----
install_python() {
    if command -v python3 &>/dev/null; then
        local py_ver
        py_ver="$(python3 --version 2>&1 | awk '{print $2}')"
        success "Python3 已安装: ${py_ver}"
        return 0
    fi

    info "安装 Python3..."
    case "$PKG_MANAGER" in
        apt)  $PKG_UPDATE && $PKG_INSTALL python3 python3-pip python3-venv ;;
        yum)  $PKG_INSTALL python3 python3-pip ;;
        apk)  $PKG_INSTALL python3 py3-pip ;;
        brew) $PKG_INSTALL python3 ;;
    esac
    success "Python3 安装完成"
}

install_supervisor() {
    if [[ "$OS_ID" == "macos" ]]; then
        # macOS 使用 launchd 或直接用 nohup，不用 supervisor
        info "macOS 将使用 nohup 后台运行模式"
        return 0
    fi

    if command -v supervisord &>/dev/null; then
        success "Supervisor 已安装"
        return 0
    fi

    info "安装 Supervisor..."
    case "$PKG_MANAGER" in
        apt) $PKG_INSTALL supervisor ;;
        yum) $PKG_INSTALL supervisor ;;
        apk) $PKG_INSTALL supervisor ;;
    esac

    # 确保 supervisor 服务启动
    if command -v systemctl &>/dev/null; then
        systemctl enable supervisord 2>/dev/null || systemctl enable supervisor 2>/dev/null || true
        systemctl start supervisord 2>/dev/null || systemctl start supervisor 2>/dev/null || true
    fi
    success "Supervisor 安装完成"
}

# ---- Python 虚拟环境 ----
setup_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        success "虚拟环境已存在: ${VENV_DIR}"
    else
        info "创建 Python 虚拟环境..."
        python3 -m venv "$VENV_DIR"
        success "虚拟环境已创建"
    fi

    info "安装 Python 依赖..."
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
    success "依赖安装完成"
}

# ---- 目录准备 ----
prepare_dirs() {
    mkdir -p "$DATA_DIR" "$LOG_DIR"
    success "数据目录: ${DATA_DIR}"
    success "日志目录: ${LOG_DIR}"
}

# ---- Supervisor 配置 ----
get_supervisor_conf_dir() {
    local dirs=(
        "/etc/supervisor/conf.d"
        "/etc/supervisord.d"
        "/etc/supervisor.d"
    )
    for d in "${dirs[@]}"; do
        if [[ -d "$d" ]]; then
            echo "$d"
            return 0
        fi
    done
    # 如果都不存在，创建默认的
    mkdir -p "/etc/supervisor/conf.d"
    echo "/etc/supervisor/conf.d"
}

setup_supervisor_conf() {
    if [[ "$OS_ID" == "macos" ]]; then
        return 0
    fi

    local conf_dir
    conf_dir="$(get_supervisor_conf_dir)"
    local conf_file="${conf_dir}/${SUPERVISOR_CONF_NAME}"

    info "生成 Supervisor 配置..."

    cat > "$conf_file" <<SUPERVISOREOF
[program:${APP_NAME}]
command=${VENV_DIR}/bin/python ${APP_DIR}/${APP_FILE}
directory=${APP_DIR}
autostart=true
autorestart=true
startsecs=5
startretries=3
stopwaitsecs=10
stopsignal=TERM
redirect_stderr=true
stdout_logfile=${LOG_DIR}/${APP_NAME}.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=5
environment=PYTHONUNBUFFERED="1"
SUPERVISOREOF

    success "Supervisor 配置已写入: ${conf_file}"

    # 重新加载 supervisor 配置
    supervisorctl reread &>/dev/null || true
    supervisorctl update &>/dev/null || true
}

# ---- macOS nohup 后台模式 ----
macos_start() {
    if is_running; then
        warn "服务已在运行中 (PID: $(cat "$PID_FILE"))"
        return 0
    fi
    info "以 nohup 模式启动服务..."
    cd "$APP_DIR"
    nohup "${VENV_DIR}/bin/python" "${APP_FILE}" \
        > "${LOG_DIR}/${APP_NAME}.log" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if is_running; then
        success "服务已启动 (PID: $(cat "$PID_FILE"))"
    else
        error "服务启动失败，请查看日志: ${LOG_DIR}/${APP_NAME}.log"
        return 1
    fi
}

macos_stop() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            success "服务已停止"
        else
            warn "进程 ${pid} 不存在"
        fi
        rm -f "$PID_FILE"
    else
        warn "PID 文件不存在，尝试查找进程..."
        local pid
        pid="$(pgrep -f "${APP_FILE}" 2>/dev/null | head -1 || true)"
        if [[ -n "$pid" ]]; then
            kill "$pid" 2>/dev/null || true
            success "服务已停止 (PID: ${pid})"
        else
            warn "未找到运行中的服务"
        fi
    fi
}

# ---- 通用服务控制 ----
is_running() {
    if [[ "$OS_ID" == "macos" ]]; then
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            return 0
        fi
        return 1
    else
        local status
        status="$(supervisorctl status "${APP_NAME}" 2>/dev/null || true)"
        if echo "$status" | grep -q "RUNNING"; then
            return 0
        fi
        return 1
    fi
}

do_start() {
    if [[ "$OS_ID" == "macos" ]]; then
        macos_start
    else
        info "启动服务..."
        supervisorctl start "${APP_NAME}" 2>/dev/null || true
        sleep 2
        if is_running; then
            success "服务已启动"
        else
            error "启动失败，请查看日志"
            do_logs
        fi
    fi
}

do_stop() {
    if [[ "$OS_ID" == "macos" ]]; then
        macos_stop
    else
        info "停止服务..."
        supervisorctl stop "${APP_NAME}" 2>/dev/null || true
        success "服务已停止"
    fi
}

do_restart() {
    info "重启服务..."
    do_stop
    sleep 1
    do_start
}

do_status() {
    header "服务状态"

    if [[ "$OS_ID" == "macos" ]]; then
        if is_running; then
            local pid
            pid="$(cat "$PID_FILE")"
            echo -e "  状态:  ${GREEN}运行中${NC}"
            echo -e "  PID:   ${pid}"
            echo -e "  端口:  ${APP_PORT}"
            echo -e "  地址:  http://localhost:${APP_PORT}"
            # 显示运行时间和内存
            ps -p "$pid" -o etime=,rss= 2>/dev/null | awk '{
                printf "  运行:  %s\n  内存:  %.1f MB\n", $1, $2/1024
            }' || true
        else
            echo -e "  状态:  ${RED}未运行${NC}"
        fi
    else
        supervisorctl status "${APP_NAME}" 2>/dev/null || echo -e "  状态:  ${RED}未配置${NC}"
    fi

    echo ""
    # 检查端口
    if command -v ss &>/dev/null; then
        local listening
        listening="$(ss -tlnp 2>/dev/null | grep ":${APP_PORT}" || true)"
        if [[ -n "$listening" ]]; then
            echo -e "  端口 ${APP_PORT}: ${GREEN}监听中${NC}"
        else
            echo -e "  端口 ${APP_PORT}: ${YELLOW}未监听${NC}"
        fi
    elif command -v lsof &>/dev/null; then
        local listening
        listening="$(lsof -i :${APP_PORT} -sTCP:LISTEN 2>/dev/null || true)"
        if [[ -n "$listening" ]]; then
            echo -e "  端口 ${APP_PORT}: ${GREEN}监听中${NC}"
        else
            echo -e "  端口 ${APP_PORT}: ${YELLOW}未监听${NC}"
        fi
    fi

    # 数据库大小
    local db_file="${DATA_DIR}/rss_news.db"
    if [[ -f "$db_file" ]]; then
        local db_size
        db_size="$(du -h "$db_file" | cut -f1)"
        echo -e "  数据库: ${db_size}"
    fi

    # 健康检查
    if command -v curl &>/dev/null; then
        local health
        health="$(curl -sf "http://localhost:${APP_PORT}/health" 2>/dev/null || true)"
        if [[ -n "$health" ]]; then
            echo -e "  健康:  ${GREEN}正常${NC}"
        else
            echo -e "  健康:  ${YELLOW}无法访问${NC}"
        fi
    fi
}

do_logs() {
    header "查看日志"
    local log_file="${LOG_DIR}/${APP_NAME}.log"
    if [[ -f "$log_file" ]]; then
        echo -e "${CYAN}最近 50 行日志:${NC}"
        tail -n 50 "$log_file"
    else
        warn "日志文件不存在: ${log_file}"
    fi
}

do_follow_logs() {
    local log_file="${LOG_DIR}/${APP_NAME}.log"
    if [[ -f "$log_file" ]]; then
        info "实时日志 (Ctrl+C 退出):"
        tail -f "$log_file"
    else
        warn "日志文件不存在: ${log_file}"
    fi
}

# ---- 完整安装 ----
do_install() {
    header "RSS News Viewer 一键安装"

    echo -e "  项目目录: ${BOLD}${APP_DIR}${NC}"
    echo -e "  服务端口: ${BOLD}${APP_PORT}${NC}"
    echo ""

    # 检查应用文件
    if [[ ! -f "${APP_DIR}/${APP_FILE}" ]]; then
        error "未找到应用文件: ${APP_DIR}/${APP_FILE}"
        exit 1
    fi
    if [[ ! -f "${APP_DIR}/requirements.txt" ]]; then
        error "未找到依赖文件: ${APP_DIR}/requirements.txt"
        exit 1
    fi

    detect_os

    if [[ "$OS_ID" != "macos" ]]; then
        check_root
    fi

    # 安装依赖
    header "安装系统依赖"
    install_python
    install_supervisor

    # 准备目录
    header "准备目录"
    prepare_dirs

    # 设置虚拟环境
    header "配置 Python 环境"
    setup_venv

    # 配置 Supervisor
    header "配置后台服务"
    setup_supervisor_conf

    # 启动服务
    header "启动服务"
    do_start

    # 完成
    echo ""
    echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
    echo -e "${GREEN}${BOLD}  ✓ RSS News Viewer 安装完成！${NC}"
    echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
    echo ""
    echo -e "  访问地址:  ${CYAN}http://localhost:${APP_PORT}${NC}"
    echo -e "  项目目录:  ${APP_DIR}"
    echo -e "  数据目录:  ${DATA_DIR}"
    echo -e "  日志文件:  ${LOG_DIR}/${APP_NAME}.log"
    echo ""
    echo -e "  管理命令:"
    echo -e "    ${BOLD}bash $0 status${NC}   - 查看状态"
    echo -e "    ${BOLD}bash $0 restart${NC}  - 重启服务"
    echo -e "    ${BOLD}bash $0 logs${NC}     - 查看日志"
    echo -e "    ${BOLD}bash $0 stop${NC}     - 停止服务"
    echo ""
}

# ---- 更新部署 ----
do_update() {
    header "更新部署"
    info "更新 Python 依赖..."
    "${VENV_DIR}/bin/pip" install --quiet --upgrade -r "${APP_DIR}/requirements.txt"
    success "依赖已更新"
    do_restart
    success "更新完成"
}

# ---- 卸载 ----
do_uninstall() {
    header "卸载 RSS News Viewer"

    echo -e "${YELLOW}警告: 此操作将停止服务并删除虚拟环境和日志${NC}"
    echo -e "${YELLOW}数据库文件将被保留在 ${DATA_DIR}${NC}"
    echo ""
    read -rp "确认卸载? (y/N): " confirm
    if [[ "${confirm,,}" != "y" ]]; then
        info "已取消"
        return 0
    fi

    # 停止服务
    do_stop

    # 删除 supervisor 配置
    if [[ "$OS_ID" != "macos" ]]; then
        local conf_dir
        conf_dir="$(get_supervisor_conf_dir)"
        rm -f "${conf_dir}/${SUPERVISOR_CONF_NAME}"
        supervisorctl reread &>/dev/null || true
        supervisorctl update &>/dev/null || true
        info "Supervisor 配置已删除"
    fi

    # 删除虚拟环境
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        info "虚拟环境已删除"
    fi

    # 删除日志
    if [[ -d "$LOG_DIR" ]]; then
        rm -rf "$LOG_DIR"
        info "日志已删除"
    fi

    # 删除 PID 文件
    rm -f "$PID_FILE"

    echo ""
    success "卸载完成"
    info "数据库文件已保留在: ${DATA_DIR}"
    info "如需彻底删除数据，请手动执行: rm -rf ${DATA_DIR}"
}

# ---- 配置信息 ----
do_config() {
    header "当前配置"
    echo -e "  应用名称:  ${APP_NAME}"
    echo -e "  应用文件:  ${APP_DIR}/${APP_FILE}"
    echo -e "  服务端口:  ${APP_PORT}"
    echo -e "  虚拟环境:  ${VENV_DIR}"
    echo -e "  数据目录:  ${DATA_DIR}"
    echo -e "  日志目录:  ${LOG_DIR}"
    echo ""
    if [[ -f "${VENV_DIR}/bin/python" ]]; then
        echo -e "  Python:    $("${VENV_DIR}/bin/python" --version 2>&1)"
        echo -e "  已安装包:"
        "${VENV_DIR}/bin/pip" list --format=columns 2>/dev/null | head -20
    fi
}

# ---- 交互式菜单 ----
show_menu() {
    echo ""
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║     RSS News Viewer 管理面板        ║${NC}"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}1)${NC} 一键安装       ${BOLD}6)${NC} 查看日志"
    echo -e "  ${BOLD}2)${NC} 启动服务       ${BOLD}7)${NC} 实时日志"
    echo -e "  ${BOLD}3)${NC} 停止服务       ${BOLD}8)${NC} 查看配置"
    echo -e "  ${BOLD}4)${NC} 重启服务       ${BOLD}9)${NC} 更新部署"
    echo -e "  ${BOLD}5)${NC} 查看状态       ${BOLD}0)${NC} 卸载"
    echo ""
    echo -e "  ${BOLD}q)${NC} 退出"
    echo ""
}

interactive_menu() {
    detect_os
    while true; do
        show_menu
        read -rp "请选择操作 [1-9/0/q]: " choice
        case "$choice" in
            1) do_install ;;
            2) do_start ;;
            3) do_stop ;;
            4) do_restart ;;
            5) do_status ;;
            6) do_logs ;;
            7) do_follow_logs ;;
            8) do_config ;;
            9) do_update ;;
            0) do_uninstall ;;
            q|Q) echo "再见！"; exit 0 ;;
            *) warn "无效选择" ;;
        esac
    done
}

# ---- 主入口 ----
main() {
    case "${1:-}" in
        install)   detect_os; do_install ;;
        start)     detect_os; do_start ;;
        stop)      detect_os; do_stop ;;
        restart)   detect_os; do_restart ;;
        status)    detect_os; do_status ;;
        logs)      detect_os; do_logs ;;
        follow)    detect_os; do_follow_logs ;;
        config)    detect_os; do_config ;;
        update)    detect_os; do_update ;;
        uninstall) detect_os; do_uninstall ;;
        menu)      interactive_menu ;;
        "")        interactive_menu ;;
        *)
            echo "用法: bash $0 {install|start|stop|restart|status|logs|follow|config|update|uninstall|menu}"
            exit 1
            ;;
    esac
}

main "$@"
