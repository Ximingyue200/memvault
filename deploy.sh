#!/usr/bin/env bash
# memvault 一键起（三个进程 + 相对数据目录）
#   用法：  ./deploy.sh            首次跑会建库、起服务
#           ./deploy.sh stop       停掉
#   依赖：python3 >= 3.10、numpy（requirements.txt）
set -euo pipefail
cd "$(dirname "$0")"

DATA="${MEMVAULT_DATA:-$PWD/data}"
mkdir -p "$DATA" "$DATA/logs"
export MEMVAULT_DB="${MEMVAULT_DB:-$DATA/memvault.db}"
export GW_DB="${GW_DB:-$MEMVAULT_DB}"

# 演示端点（免费、无需 key）。换成自己的 API 只需设这两个变量，或在面板里改。
export MEMVAULT_LLM_URL="${MEMVAULT_LLM_URL:-https://text.pollinations.ai/openai}"
export MEMVAULT_LLM_MODEL="${MEMVAULT_LLM_MODEL:-openai}"
export MEMVAULT_LLM_KEY="${MEMVAULT_LLM_KEY:-}"
# 向量服务（可选）：不配就退化成纯 BM25，只是召回糙一点
export MEMVAULT_EMBED_URL="${MEMVAULT_EMBED_URL:-}"

PIDF="$DATA/pids"
stop() {
  if [ -d "$PIDF" ]; then
    for f in "$PIDF"/*.pid; do [ -e "$f" ] || continue; kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
  fi
  echo "已停止"
}
case "${1:-start}" in
  stop) stop; exit 0 ;;
esac

stop >/dev/null 2>&1 || true
declare -A PORTS=( [server]=4174 [gateway]=4180 [mcp_server]=4181 )
for m in server gateway mcp_server; do
  nohup python3 -m "memvault.$m" > "$DATA/logs/$m.log" 2>&1 &
  echo $! > "$PIDF.$m.pid"
  mkdir -p "$PIDF"; mv "$PIDF.$m.pid" "$PIDF/$m.pid"
  echo "  起 memvault.$m  pid=$!  log=$DATA/logs/$m.log"
done

sleep 3
echo
echo "  面板      http://127.0.0.1:4174/admin/     （旧界面 / 会自动跳到 :4180/admin）"
echo "  网关      http://127.0.0.1:4180/v1        （带记忆注入）"
echo "  纯净入口  http://127.0.0.1:4182/v1        （不注入，只转发）"
echo "  MCP       http://127.0.0.1:4181/mcp"
echo "  数据      $MEMVAULT_DB"
echo
echo "  演示 API：text.pollinations.ai（免费、慢、会限流）。换成自己的："
echo "    export MEMVAULT_LLM_URL=https://api.deepseek.com  MEMVAULT_LLM_MODEL=deepseek-flash  MEMVAULT_LLM_KEY=sk-..."
echo "  停止： ./deploy.sh stop"
