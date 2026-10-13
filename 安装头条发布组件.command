#!/bin/zsh
set -eu
cd "${0:A:h}"
PUBLISH_BASE_PYTHON="${SHENGYING_PYTHON:-$PWD/.venv/bin/python}"
"$PUBLISH_BASE_PYTHON" -m venv .publish-venv
.publish-venv/bin/python -m pip install -r requirements-publish.txt
echo '头条自动上传组件安装完成。请点击成片旁的“发布到今日头条”，首次在 Chrome 发布窗口登录。'
