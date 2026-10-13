#!/bin/zsh
set -eu
setopt NO_BG_NICE
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PRODUCT_PYTHON="${SHENGYING_PYTHON:-$PWD/.venv/bin/python}"
PRODUCT_URL="http://127.0.0.1:8092"
show_page() {
  if [[ "${WORKBENCH_NO_OPEN:-0}" != "1" ]]; then
    open "$PRODUCT_URL"
  fi
}
if [[ ! -x "$PRODUCT_PYTHON" ]]; then
  echo "缺少独立 Python 环境，请按 README 安装。"
  exit 1
fi
healthy() {
  curl --noproxy '*' -fsS --max-time 2 "$PRODUCT_URL/api/health" 2>/dev/null |
    "$PRODUCT_PYTHON" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("product")=="tingwen-shengying-workbench" else 1)' 2>/dev/null
}
if healthy; then
  show_page
  exit 0
fi
if lsof -nP -iTCP:8092 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8092 端口被其他程序占用；没有停止或修改该程序。"
  exit 1
fi
nohup "$PRODUCT_PYTHON" -B -u app.py --port 8092 > workbench.log 2>&1 < /dev/null &
PRODUCT_PID=$!
for PRODUCT_ATTEMPT in {1..30}; do
  if healthy; then
    show_page
    exit 0
  fi
  if ! kill -0 "$PRODUCT_PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done
echo "启动失败，请查看本目录中的 workbench.log。"
exit 1
