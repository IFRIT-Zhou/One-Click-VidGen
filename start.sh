#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/runtime/logs"

PYTHON_BIN="${PYTHON_BIN:-python}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
if [[ -n "${FRONTEND_PORT+x}" ]]; then
  FRONTEND_PORT_EXPLICIT=1
else
  FRONTEND_PORT_EXPLICIT=0
fi
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
BACKEND_LOG="$LOG_DIR/backend-dev.log"
FRONTEND_LOG="$LOG_DIR/frontend-dev.log"

mkdir -p "$LOG_DIR"
cd "$ROOT_DIR"

STARTED_PIDS=()
STARTED_AS_GROUP=()
CLEANED_UP=0

cleanup() {
  if [[ "$CLEANED_UP" -eq 1 ]]; then
    return
  fi
  CLEANED_UP=1

  if [[ "${#STARTED_PIDS[@]}" -gt 0 ]]; then
    echo
    echo "[停止] 正在关闭本次启动的服务..."
  fi

  local index pid grouped
  for index in "${!STARTED_PIDS[@]}"; do
    pid="${STARTED_PIDS[$index]}"
    grouped="${STARTED_AS_GROUP[$index]}"
    if kill -0 "$pid" 2>/dev/null; then
      if [[ "$grouped" -eq 1 ]]; then
        kill -TERM -- "-$pid" 2>/dev/null || true
      else
        kill -TERM "$pid" 2>/dev/null || true
      fi
    fi
  done

  for pid in "${STARTED_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

trap cleanup EXIT
trap 'exit 130' INT TERM

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "[错误] 缺少命令：$command_name" >&2
    exit 1
  fi
}

port_in_use() {
  "$PYTHON_BIN" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

backend_ready() {
  curl --noproxy '*' -fsS --max-time 2 "$BACKEND_URL/api/health" 2>/dev/null \
    | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}

frontend_ready() {
  curl --noproxy '*' -fsS --max-time 2 "$FRONTEND_URL/" 2>/dev/null \
    | grep -q '口播视频生成台'
}

start_service() {
  local log_path="$1"
  shift

  echo "[$(date '+%F %T')] $*" >>"$log_path"
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" >>"$log_path" 2>&1 &
    STARTED_PIDS+=("$!")
    STARTED_AS_GROUP+=("1")
  else
    "$@" >>"$log_path" 2>&1 &
    STARTED_PIDS+=("$!")
    STARTED_AS_GROUP+=("0")
  fi
}

wait_until_ready() {
  local name="$1"
  local check_function="$2"
  local timeout_seconds="$3"
  local elapsed

  for ((elapsed = 0; elapsed < timeout_seconds; elapsed++)); do
    if "$check_function"; then
      echo "[就绪] $name"
      return 0
    fi
    sleep 1
  done

  echo "[错误] $name 在 ${timeout_seconds} 秒内未就绪。" >&2
  return 1
}

require_command "$PYTHON_BIN"
require_command npm
require_command curl

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "[提示] 未找到 .env，将使用项目默认配置。"
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "[错误] 前端依赖尚未安装，请先执行：cd frontend && npm install" >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/node_modules/hyperframes" ]]; then
  echo "[错误] Hyperframes 尚未安装，请先在项目根目录执行：npm install" >&2
  exit 1
fi

echo "[检查] Python 依赖与 MySQL..."
"$PYTHON_BIN" - <<'PY'
try:
    import fastapi  # noqa: F401
    import pymysql  # noqa: F401
    import uvicorn  # noqa: F401
except ImportError as exc:
    raise SystemExit(f"缺少 Python 依赖：{exc}。请执行 pip install -r requirements.txt")

from backend.app.db import db_status, init_database
from backend.app.pipeline import resolve_asr_python

init_database()
status = db_status()
if not status["ready"]:
    raise SystemExit(f"MySQL 不可用：{status['last_error']}")
print(f"[就绪] MySQL {status['host']}:{status['port']}/{status['database']}")
print(f"[就绪] Faster-Whisper {resolve_asr_python()}")
PY

echo "[检查] RunningHub TTS..."
if "$PYTHON_BIN" - <<'PY'
from backend.app.runninghub_tts import load_runninghub_tts_config

config = load_runninghub_tts_config()
if config:
    print(f"[就绪] RunningHub minimax/speech-2.8-hd (voice_id={config.voice_id})")
else:
    print("[警告] RunningHub TTS 未启用或未配置 API Key")
raise SystemExit(0 if config else 1)
PY
then
  :
else
  echo "[警告] RunningHub TTS 当前不可用，应用仍会启动，但生成任务会失败。"
fi

REUSE_BACKEND=0
REUSE_FRONTEND=0

if backend_ready; then
  REUSE_BACKEND=1
  echo "[复用] 后端已运行：$BACKEND_URL"
elif port_in_use "$BACKEND_PORT"; then
  echo "[错误] 端口 $BACKEND_PORT 已被其他服务占用。" >&2
  exit 1
fi

if frontend_ready; then
  REUSE_FRONTEND=1
  echo "[复用] 前端已运行：$FRONTEND_URL"
elif port_in_use "$FRONTEND_PORT"; then
  if [[ "$FRONTEND_PORT_EXPLICIT" -eq 0 && "$FRONTEND_PORT" -eq 5173 ]]; then
    FRONTEND_PORT=5174
    FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
    echo "[提示] 5173 已被其他服务占用，自动切换前端到 5174。"
    if frontend_ready; then
      REUSE_FRONTEND=1
      echo "[复用] 前端已运行：$FRONTEND_URL"
    elif port_in_use "$FRONTEND_PORT"; then
      echo "[错误] 端口 5173 和 5174 均已被其他服务占用。" >&2
      exit 1
    fi
  else
    echo "[错误] 端口 $FRONTEND_PORT 已被其他服务占用。" >&2
    echo "       可关闭占用进程，或指定其他端口：FRONTEND_PORT=5174 ./start.sh" >&2
    exit 1
  fi
fi

if [[ "$REUSE_BACKEND" -eq 0 ]]; then
  echo "[启动] 后端：$BACKEND_URL"
  start_service \
    "$BACKEND_LOG" \
    "$PYTHON_BIN" -m uvicorn backend.app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT"
  if ! wait_until_ready "后端" backend_ready 30; then
    tail -n 40 "$BACKEND_LOG" >&2 || true
    exit 1
  fi
fi

if [[ "$REUSE_FRONTEND" -eq 0 ]]; then
  echo "[启动] 前端：$FRONTEND_URL"
  start_service \
    "$FRONTEND_LOG" \
    bash -c 'cd "$1" && exec npm run dev -- --host "$2" --port "$3"' \
    _ "$FRONTEND_DIR" "$FRONTEND_HOST" "$FRONTEND_PORT"
  if ! wait_until_ready "前端" frontend_ready 30; then
    tail -n 40 "$FRONTEND_LOG" >&2 || true
    exit 1
  fi
fi

echo
echo "=========================================="
echo "  口播视频生成台已启动"
echo "  前端：$FRONTEND_URL"
echo "  后端：$BACKEND_URL"
echo "  后端日志：$BACKEND_LOG"
echo "  前端日志：$FRONTEND_LOG"
echo "=========================================="

if [[ "${#STARTED_PIDS[@]}" -eq 0 ]]; then
  echo "[完成] 服务均已在运行。"
  exit 0
fi

echo "按 Ctrl+C 停止本次启动的服务。"
while true; do
  for index in "${!STARTED_PIDS[@]}"; do
    if ! kill -0 "${STARTED_PIDS[$index]}" 2>/dev/null; then
      echo "[错误] 服务进程 ${STARTED_PIDS[$index]} 已退出，请检查日志。" >&2
      exit 1
    fi
  done
  sleep 2
done
