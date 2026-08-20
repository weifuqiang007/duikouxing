#!/usr/bin/env bash
# 云服务器 InfiniteTalk 实验环境：venv + pypi 依赖（容器无 conda/pip3，走 ensurepip）。
# 前置：external/InfiniteTalk 源码已上传到服务器（本地无法直连 github，见 README）。
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-}"
if [[ -z "$PROJECT_ROOT" ]]; then
  for d in "$HOME/duikouxing" /root/duikouxing /root/siton-tmp/duikouxing; do
    [[ -d "$d/external/InfiniteTalk" ]] && PROJECT_ROOT="$d" && break
  done
fi
[[ -n "$PROJECT_ROOT" && -d "$PROJECT_ROOT/external/InfiniteTalk" ]] || {
  echo "ERROR: 未找到 external/InfiniteTalk，请先上传源码并设置 PROJECT_ROOT" >&2
  exit 2
}
IT_ROOT="$PROJECT_ROOT/external/InfiniteTalk"
ENV="$PROJECT_ROOT/.conda-envs/infinitetalk"

export PIP_CACHE_DIR="$PROJECT_ROOT/.cache/pip"
export TMPDIR="$PROJECT_ROOT/.tmp"
mkdir -p "$PIP_CACHE_DIR" "$TMPDIR"

# torch 2.4.1 用 pypi 的 cu121 构建（服务器到 download.pytorch.org 不稳定）。
# 容器缺 python3-venv 的 ensurepip 组件：--without-pip 建 venv，再经 get-pip 引导。
if [[ ! -x "$ENV/bin/python" ]]; then
  python3 -m venv "$ENV" 2>/dev/null || python3 -m venv --without-pip "$ENV"
fi
PY="$ENV/bin/python"
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$TMPDIR/get-pip.py"
  "$PY" "$TMPDIR/get-pip.py" -q
fi

if ! "$PY" -c "import torch" 2>/dev/null; then
  "$PY" -m pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1
fi
if ! "$PY" -c "import xformers" 2>/dev/null; then
  "$PY" -m pip install -U xformers==0.0.28 || echo "WARN: xformers 安装失败，将走 sdpa 回退"
fi

# flash_attn：pypi 有 torch2.4+cu12+py310 预编译轮子；失败不阻塞（attention.py 有 sdpa 回退）。
"$PY" -m pip install ninja psutil packaging wheel -q
"$PY" -m pip install flash_attn==2.7.4.post1 --no-build-isolation \
  || echo "WARN: flash_attn 安装失败，将走 sdpa 回退（速度变慢）"

# 官方 requirements；xfuser（多 GPU 用）和 gradio（web demo 用）失败不阻塞。
grep -vE '^(xfuser|gradio)' "$IT_ROOT/requirements.txt" > "$TMPDIR/infinitetalk-req.txt"
"$PY" -m pip install -r "$TMPDIR/infinitetalk-req.txt" \
  || echo "WARN: 部分 requirements 安装失败，逐个补装关键项"
"$PY" -m pip install librosa pyloudnorm soundfile || true

"$PY" - <<'PYEOF'
import torch
print(f"torch {torch.__version__}, cuda {torch.version.cuda}, "
      f"gpu可用={torch.cuda.is_available()}")
for mod in ("flash_attn", "xformers", "decord", "librosa", "pyloudnorm", "moviepy"):
    try:
        __import__(mod)
        print(f"  {mod}: OK")
    except Exception as e:
        print(f"  {mod}: 缺失/失败 ({e.__class__.__name__})")
PYEOF
echo "InfiniteTalk 环境就绪: $ENV"
