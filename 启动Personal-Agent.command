#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x ".venv/bin/python" ]]; then
  print "请先按 docs/local-use.md 准备本机 Python 环境。"
  read -r "?按回车关闭本窗口。"
  exit 1
fi
exec .venv/bin/python scripts/local_app.py start --pause
