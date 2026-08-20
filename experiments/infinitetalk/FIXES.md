# InfiniteTalk 云端 4090 环境修复清单（2026-08-20 实测全通）

一次跑通踩的坑，按出现顺序。重装环境照此执行即可，全部已在服务器验证。

## 1. 依赖版本（venv: .conda-envs/infinitetalk, torch 2.4.1+cu121 已预装）

官方 requirements.txt 缺包 + 版本开区间放进了不兼容新版，最终锁定组合：

```bash
PY=.conda-envs/infinitetalk/bin/pip
$PY install einops omegaconf                                  # 官方 requirements 漏了
$PY install xfuser==0.4.1                                     # 代码硬 import（multitalk_utils），单卡也要装
$PY install "diffusers==0.33.1"                               # 关键！0.36+ 的 attention_dispatch 在 torch2.4 上 schema 崩
$PY install "transformers==4.49.0"                            # 关键！5.x 删了 FLAX_WEIGHTS_NAME，diffusers 0.31~0.33 必 4.x
$PY install "misaki[en,espeak]"                               # kokoro TTS g2p，generate 脚本 import 链硬依赖
# xfuser 会顺手装个不兼容的 flash-attn（schema 冲突）→ 立即卸载：
$PY uninstall -y flash-attn
```

版本三角：torch 2.4.1 + diffusers 0.33.1 + transformers 4.49.0 是唯一实测通过组合。
transformers 5.15 与 4.x + diffusers 0.31/0.39 均失败，别再试。

## 2. 系统层（root 容器，Ubuntu 22.04）

```bash
apt-get install -y python3.10-dev        # quanto_cuda JIT 需要 Python.h
ln -sf $ENV/bin/ninja /usr/local/bin/ninja       # torch cpp_extension 找 PATH 里的 ninja
ln -sf /usr/local/cuda-12.1/bin/nvcc /usr/local/bin/nvcc
# 运行时必须带：CUDA_HOME=/usr/local/cuda-12.1 PATH=/usr/local/cuda-12.1/bin:$PATH
```

optimum-quanto 0.2.6 首次运行 JIT 编 quanto_cuda（9 个 fp8 marlin 算子，~4 分钟），编译成功后缓存在 ~/.cache/torch_extensions。

## 3. 代码补丁：sdpa 回退（见 infinitetalk-sdpa-fallback.patch）

flash_attn 2 无预编译轮子（torch2.4+cu121+py310 源码编译 30-120 分钟，不值得），
但 CLIP/DiT 是直调 `flash_attention()`（FA3/FA2 二选一，assert 硬崩），不走自带的
`attention()` 分发器（该分发器才有 sdpa 兜底）。修法：三个文件里直调全部换成
`attention()`，签名兼容（k_lens/causal/dropout_p 均支持）：

- wan/modules/clip.py：85 行（去 version=2）、197 行、导入行
- wan/modules/model.py：149/179/220/222、导入行
- wan/modules/multitalk_model.py：157/204/206、导入行（注意还有 sageattn 分支，未启用无影响）

注意：sed 带 `$` 锚点匹配不上这些文件（CRLF 行尾），用无锚点模式。
V2V 用的是 multitalk_model.py（MultiTalkModel），model.py 是原版 WanModel。

## 4. 下载脚本坑

- huggingface-cli 0.30.2 不支持 `download repo 文件名 --revision refs/pr/1` 语法 →
  wav2vec fp16 用 curl 直拉：`https://hf-mirror.com/TencentGameMate/chinese-wav2vec2-base/resolve/refs%2Fpr%2F1/model.safetensors`
- 服务器到 hf-mirror 实测 ~25MB/s，31GB 权重约 25 分钟

## 5. 实测性能（4090, 14s/480P, 40 步, fp8+层流式+sdpa）

- 去噪 ~98s/步 → 40 步约 65 分钟；显存峰值 ~11GB（--num_persistent_param_in_dit 0 生效）
