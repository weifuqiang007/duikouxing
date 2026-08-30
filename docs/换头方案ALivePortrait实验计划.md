# 换头方案 A 实验计划：肖像生成 → LivePortrait 再演 → 回贴合成

> 日期：2026-08-28
> 业务背景：见 [业务诉求.md](业务诉求.md)（业务线 2：换头）
> 服务器：`ssh -p 37911 root@219.147.100.42`（FaceFusion 机，RTX 4090 24GB，费率 2 元/小时）
> 备用机：`219.147.100.42:34300`（DreamID-V 容器，大图像模型磁盘不够时用）

---

## 一、目标与总架构

**目标**：工作人员录制的宣读视频中，人物整颗头（脸+脸型轮廓+发型+耳朵）替换为身份证照片人物的头；头部动作/表情/口型跟随原视频；脖子以下、背景、语音不动。

```
11.png (身份证照) ──①肖像归一化──▶ portrait.png（完整发型/露耳/1024+/光照均匀）
                                        │
wlh.mp4 (工作人员视频) ────────②LivePortrait 再演──▶ animated_head.mp4
                                        │                （肖像被驱动，口型表情来自视频）
wlh.mp4 ──③回贴合成─────────────────────────────────────▶ final.mp4
          分割原头 → 背景底板补洞 → 关键点对齐贴新头 → 羽化+肤色传递
```

**测试素材**：`/root/siton-tmp/dreamidv_input/11.png`（身份证照）+ `wlh.mp4`（720×1280, 30fps, 13.68s, 409 帧）。

## 二、闸门与验收标准（先证明再工程化，每关不过不进下一关）

| 闸门 | 内容 | 通过标准 | 不过怎么办 |
|---|---|---|---|
| **G1** | 再演保真（用原始 11.png 直跑） | ①发型/耳朵/脸型与照片一致；②头部动作与驱动视频时间对齐；③无明显抖动闪烁；④口型有节奏（不需要完美，此线语音本来就对口） | 换 G2 的增强肖像重试；仍不过 → 复查 LivePortrait 512 版 / 换驱动视频；再失败 → 回退方案 B |
| **G2** | 肖像归一化质量 | 身份一致性（与证件照并排主观分最高）、发型完整、≥1024px、无明显高光 | 保持 L1 简单预处理路线，不硬上大模型 |
| **G3** | 回贴合成质量 | 正常播放速度下看不出脖子/发际接缝；409 帧无闪烁；肤色无明显断层 | 迭代羽化宽度/边界位置/色彩传递算法 |
| **G4** | 端到端样片终审 | 与 FaceFusion v2、DreamID-V 样片并排：形象不差于 DreamID-V，穿帮不多于 v2 | 带问题清单进入第二轮调参或决策回退 |

## 三、阶段 0：环境搭建（~1-2 小时）

### 3.1 磁盘与依赖检查

```bash
# 37911 机：22GB overlay，已用 ~6GB，余 ~16GB
df -h /root
# LivePortrait 权重约 2GB，环境约 3GB —— 宽裕
# 大图像模型（InstantID ~13GB / FLUX.1-Kontext-dev fp8 ~12GB）装得下一个，装不下两个
# 不够就用 34300 机
```

### 3.2 LivePortrait 环境

```bash
conda create -n liveportrait python=3.10 -y
source /opt/conda/bin/activate liveportrait

# 代码：github 慢就走 ghproxy
git clone https://github.com/KwaiVGI/LivePortrait.git /root/siton-tmp/LivePortrait
# git clone https://ghproxy.com/https://github.com/KwaiVGI/LivePortrait.git /root/siton-tmp/LivePortrait

cd /root/siton-tmp/LivePortrait
pip install -r requirements.txt   # 阿里镜像已在全局 pip 配置

# 权重：huggingface.co 直连不通，必须走镜像
export HF_ENDPOINT=https://hf-mirror.com
bash download_weights.sh          # base(256) + insightface 模型
# 512 高清版权重（推荐）：huggingface.co/kleinzee/LivePortrait_512 手动下载后放入 pretrained/
```

注意事项：
- torch 走 CUDA 12.x 轮子（与 onnxruntime-gpu 共存无冲突，独立 conda 环境）
- T5/大依赖无，LivePortrait 是轻量级（appearance/motion extractor + SPADE + warping），4090 上单帧 ~70ms（256 模式）

### 3.3 复用 FaceFusion 资产（同机）

- `bisenet_resnet_34.onnx`：头部分割（**含 hair 类**），直接用 onnxruntime 加载，不依赖 facefusion 代码
- `2dfan4.onnx`：68 点关键点，用于回贴对齐
- `ffmpeg` 9.0.1：已链接到系统 PATH

## 四、阶段 1（G1）：再演闸门实验

**目的**：证明"照片的头"能被视频驱动起来且保真——整条路线成立与否的关键。

```bash
cd /root/siton-tmp/LivePortrait
source /opt/conda/bin/activate liveportrait

python inference.py \
  -s /root/siton-tmp/dreamidv_input/11.png \
  -v /root/siton-tmp/dreamidv_input/wlh.mp4 \
  -o /root/siton-tmp/headswap_output/g1_raw/ \
  --flag_stitching          # 具体 flag 以 README 为准，driving 默认即 relative
```

**观察清单**（逐项记录进实验记录表）：
1. 发型轮廓、耳朵、脸型是否还是 11.png 的人（并排截图对比）
2. 头部转动/点头是否跟随 wlh.mp4 的时间轴
3. 口型开合节奏是否与说话对得上（慢放逐秒看）
4. 256 vs 512 两个版本各跑一遍，对比头发纹理
5. 大动作帧（如有转头）是否存在身份漂移/头发扭曲

**产物**：`g1_raw/` 动画序列 + 观察记录。预期耗时：环境 1.5h + 实验观察 1h。

## 五、阶段 2（G2）：肖像归一化对比

**目的**：把"身份证照"洗成高质量标准肖像。四档由轻到重，逐档对比，够用即停：

| 档 | 方法 | 成本 | 说明 |
|---|---|---|---|
| **L0** | 原始 11.png | 0 | 基线（G1 已跑） |
| **L1** | 预处理增强：频率分离去高光 + Real-ESRGAN ×2 + 简单底色统一 | 分钟级，磁盘 <1GB | **优先做**；gfpgan_1.4.onnx 下载后可加一步面部修复 |
| **L2** | InstantID（SDXL）生成标准肖像，姿态控制 | 磁盘 ~13GB，单图 ~30s | 身份保持强，可控角度/光照 |
| **L3** | FLUX.1-Kontext-dev fp8 指令编辑（"此人的完整正面肖像，保留发型五官，均匀柔光"） | 磁盘 ~12GB，单图 ~1min | 质化上限最高；磁盘不够放 34300 机 |

```bash
# L1 示例（37911 机，facefusion 环境内即可）
# 去高光：频率分离（低频/高频分离后压低高频高光区）——脚本待写 src/headshot/delight.py
# 放大：realesrgan-ncnn-vulkan 或 python 包
# L1 产物: portrait_L1.png
```

**判定方式**：L0/L1/L2(L3) 各出一张肖像 → 分别过一遍 LivePortrait → 三段动画并排看片打分（身份一致性 / 发型完整度 / 头发纹理）。

**注意**：L2/L3 是生成式，有身份漂移风险——**身份证业务宁可分辨率低也不能换脸**，判定时身份一致性权重最高。

## 六、阶段 3（G3）：回贴合成脚本

**新建 `src/headswap/`**，四个模块（一天工作量）：

### 6.1 头部分割 `segment_head.py`

- 逐帧（或隔帧+插值）跑 `bisenet_resnet_34.onnx`
- 头掩码 = skin ∪ 五官 ∪ 耳朵 ∪ **hair**（CelebAMask-HQ 类别表，以模型实际输出为准验证）
- **neck / cloth 类保留原视频**——脖子不换，边界收在下颌线/发际，比换到肩膀安全（衣服对不上）

### 6.2 背景底板 `build_plate.py`

- 前提：固定机位（wlh.mp4 需先确认；拍摄规范已要求静态背景）
- 做法：抽 N≈50 帧全片分布，对每像素取**非头区域像素中位数** → 单张背景底板
- 之后每帧 = 底板填洞 + 贴新头，时序绝对稳定不闪烁

### 6.3 对齐贴回 `composite.py`（核心）

- 每帧两组 68 点关键点（`2dfan4.onnx`）：原视频帧 × LivePortrait 输出帧
- 取**下颌线 + 眉毛 + 鼻梁**点（**不含嘴巴**——口型本来就要不一样）做 Umeyama 相似变换（scale/rot/tx/ty）
- 新头图与头掩罩同变换 warp → 贴到底板帧上
- 羽化：掩罩腐蚀 5px + 高斯羽化 10-20px，边界尽量沿衣领/下颌走向

### 6.4 肤色传递 `color_transfer.py`

- LAB 空间 Reinhard：用新头 skin 类像素 vs 原视频 skin/neck 类像素做均值方差匹配
- 若 6.1 肖像已在 L2/L3 做过光照匹配，此步力度调小

### 6.5 编码

- 保持 30fps / 720×1280 / 原音频流 copy，CRF 13-15，**只编码一次**（管线中间环节用无损/高码率中间格式，避免多代压缩）

**产物**：`final_g3.mp4` + 逐项 QA 记录（接缝/闪烁/肤色，正常速+慢放各看一遍）。

## 七、阶段 4（G4）：端到端样片三方评审

并排三条，送内部/客户评审：

| 样片 | 来源 | 已有 |
|---|---|---|
| A | FaceFusion v2（swap_v2.mp4） | ✅ 已有 |
| B | DreamID-V 换脸结果（客户觉得形象好的那条） | ✅ 已有 |
| C | 方案 A final.mp4 | 本实验产出 |

评审维度：形象与照片一致性（脸型+发型）、穿帮程度（接缝/耳朵/闪烁）、整体自然度、口型（B 项口型错是已知短板，仅作形象参照）。

## 八、时间表

| 日期 | 内容 | 闸门 |
|---|---|---|
| 08-28 | 阶段 0 环境 + 阶段 1 再演实验 | G1 |
| 08-29 | 阶段 2 肖像归一化对比（L1 必做，L2/L3 看磁盘） | G2 |
| 08-30 | 阶段 3 回贴合成脚本 | G3 |
| 08-31 | 阶段 4 端到端样片 + 三方评审 | G4 |

## 九、成本估算

- **实验期**：~4 天 × 2-4 GPU·时 ≈ 10-15 时 × 2 元 ≈ **20-30 元**
- **量产后单条**（13.7s 竖屏）：肖像归一化每客户一次性 ~1 分钟 + LivePortrait ~1 分钟 + 分割合成编码 ~1-2 分钟 ≈ **单条 < 5 分钟，计算成本 < 0.2 元**；对比 FaceFusion v2 的 18 秒/条，本方案慢一个量级但仍远快于 DreamID-V 的 25 分钟/条

## 十、风险与回退

| 风险 | 概率 | 影响 | 预案 |
|---|---|---|---|
| G1 再演身份/口型不保真 | 中 | 路线失败 | 换 512 权重 / 换肖像；仍败 → 方案 B（DreamID-V 裁剪域） |
| L2/L3 生成肖像身份漂移 | 中 | 肖像不可用 | 退 L1 纯预处理（无生成，身份绝对保真） |
| 脖子接缝/肤色断层 | 高 | 可修 | 边界位置/羽化宽度/色彩传递迭代，G3 预留调参轮次 |
| 头发边缘分割抖动（发丝细碎） | 中 | 边缘闪烁 | 掩罩时序平滑（对掩罩做 EMA）或收小 hair 类膨胀 |
| 相机非严格静止 | 低 | 底板法失效 | 先视频稳像；或退化 per-frame inpaint（时序风险↑） |
| 大动作帧头部漂移 | 低 | 单帧穿帮 | 拍摄规范约束；异常帧检测→用相邻帧替代 |

## 十一、交付物清单

- [ ] G1-G4 实验记录（配置编号 hs-01~，含参数与观察结论，仿 wlh-004 格式）
- [ ] `src/headswap/`：segment_head / build_plate / composite / color_transfer 四模块脚本
- [ ] 标准肖像生成 SOP（选定 L 档位 + 固定 prompt/参数）
- [ ] 端到端样片 final.mp4 + 三方对比结论
- [ ] 拍摄规范一页纸（见 业务诉求.md 第八节，随样片交付业务侧）
- [ ] 本文档更新实验结论与量产参数
