# 端到端操作手册（本机 E:\duikouxing）

从"一段源视频"到"成片"的完整操作顺序。环境装好后，日常只用第 1～4 步。
详细录制规范见 [RECORDING_GUIDE.md](RECORDING_GUIDE.md)，验收标准见 [ACCEPTANCE.md](ACCEPTANCE.md)。

> 全程用 **PowerShell**。本机 Profile 固定用 `home`（公司机器用 `office`）。

## 第 0 步：进入项目目录

```powershell
cd E:\duikouxing
```

一次性环境准备（新机器才需要，本机已装好）：`setup_conda.ps1` → `download_voice_model.ps1`
→ `setup_liveportrait.ps1` → `download_liveportrait_models.ps1`，见 [SELFTEST_PLAN.md](SELFTEST_PLAN.md) 第 0 节。

## 第 1 步：录源视频（扮演"客户"的人，即本人）

手机固定、正脸、均匀光、1080p。**逐字说出配置里的参考句**（`config\job.local.yaml` 的
`reference_text`，一个字不能差），开头闭嘴 0.5 秒，说完自然停 2 秒，总时长约 18 秒。

复制进项目并确认配置指向它：

```powershell
Copy-Item <源视频路径> .\samples\source.mp4
```

**文件名不限**，流水线只认 `config\job.local.yaml` 里 `source_video:` 指到的路径——
叫 `wlh.mp4` 也行，改成 `source_video: "../samples/wlh.mp4"` 即可；`source.mp4` 只是默认约定。
同理驱动视频名也不限，改 `driving_video:` 指向即可。

## 配置文件可以有多份

`-Job` 参数传哪个配置就跑哪个。不同人/不同话术各建一份（如 `config\job.wife.yaml`），
第 2～4 步把 `-Job`/`--job` 换成对应文件即可。注意各配置的 `job_id` 不要相同——
它决定输出目录（`jobs-home\<job_id>\`）和阶段缓存复用。`job.local*.yaml` 已被
gitignore，本地随便加不入库。

## 第 2 步：生成导读音频 + KTV 逐字字幕视频

```powershell
.\scripts\prepare_driving.ps1 -Profile home -Job E:/duikouxing/config/job.local.yaml
```

产物在 `jobs-home\<job_id>\input\`（当前 job_id 为 `selftest-002`）：

- `recording_guide.wav` —— 用本人音色克隆读出的新话术（`script` 字段那句）
- `recording_script.txt` —— 文字版

再烧录 KTV 字幕视频（白色未读/金色已读，逐字高亮，唱完一句上一句消失）：

```powershell
.conda-envs\digital-human\python.exe scripts\make_ktv_guide.py --profile home --job E:/duikouxing/config/job.local.yaml
```

产物：`jobs-home\<job_id>\input\recording_guide_ktv.mp4`。

**必须先完整试听/试看一遍**：音色不像、有错字吞字时不要往下走，改 `script` 后重跑
（同 job_id 会自动复用未变的阶段；若结果未刷新，加 `-Force`）。

## 第 3 步：录制驱动视频（操作员跟 KTV 字幕视频录）

操作员（如老婆）在电脑/手机上循环播放 `recording_guide_ktv.mp4`，**对着摄像头逐字跟随字幕动嘴**，
说的是 `script` 那句新话术，不是参考句：

- 戴单边耳机，轻声跟读或无声对口型均可——驱动视频的音轨会被丢弃，只有嘴部节奏重要。
- 跟着字幕金色进度走；抢拍、拖拍、漏字无法修复，感觉错了整段重录。
- 总时长与导读相差 12% 以内会被自动整体校正。
- 开头闭嘴正脸 0.5 秒，结束闭嘴 0.5 秒；不转头不点头；1080p、关美颜滤镜 HDR。

复制进项目并接入配置：

```powershell
Copy-Item <驱动视频路径> .\samples\driving.mp4
```

然后把 `config\job.local.yaml` 里的 `driving_video` 从 `null` 改为：

```yaml
driving_video: "../samples/driving.mp4"
```

## 第 4 步：执行主脚本，生成成片

```powershell
.\scripts\run_job.ps1 -Profile home -Job E:/duikouxing/config/job.local.yaml
```

流水线：LivePortrait 把驱动视频的口型/表情迁移到源视频 → 换回源视频画面背景 → 合成
克隆音色的导读音轨。GPU 推理约 1 分半（40 秒素材）。

成片：`jobs-home\<job_id>\output\final.mp4`。

```powershell
cmd /c start "" E:\duikouxing\jobs-home\selftest-002\output\final.mp4
```

## 常见问题

| 情况 | 处理 |
|---|---|
| 改了 `script`/`reference_text` 但产物没变 | 同 job_id 有指纹缓存，重跑加 `-Force`，或换新 `job_id` |
| 驱动视频时长差太多被拒绝 | 差 12% 以内才自动校正，重新跟录 |
| 任何一步失败 | 看 `jobs-home\<job_id>\logs\` 对应日志（如 `liveportrait.log`） |
| 下一轮全新测试 | 换 `job_id`（如 `selftest-003`），旧 job 目录保留作对照 |

参数 A/B（`animation_region`、`driving_multiplier`）见 [SELFTEST_PLAN.md](SELFTEST_PLAN.md) 第 6 节。
