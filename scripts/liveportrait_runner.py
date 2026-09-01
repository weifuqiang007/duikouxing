"""LivePortrait 官方 inference.py 的启动包装（仅标准库，运行在 liveportrait 环境）。

用法：python liveportrait_runner.py <inference.py 路径> [原样传递的参数...]

本机多线程 libx264 编码存在间歇性段错误（imageio 自带 ffmpeg 4.2.2 与系统
ffmpeg 7.0.2 均会触发，20 核心 40 线程 CPU），LivePortrait 通过 imageio 写
mp4 时无法指定线程数，这里在 imageio.get_writer 里强制注入 -threads 1。
不打补丁到 external/LivePortrait 仓库本身，避免固定提交的 checkout 覆盖。
"""

import runpy
import sys
import csv
import json
import atexit
from pathlib import Path


def _force_single_thread_encode() -> None:
    import imageio

    def patched(get_writer):
        def wrapper(*args, **kwargs):
            params = list(kwargs.get("ffmpeg_params") or [])
            if "-threads" not in params:
                params += ["-threads", "1"]
            kwargs["ffmpeg_params"] = params
            return get_writer(*args, **kwargs)

        return wrapper

    for namespace in (imageio, getattr(imageio, "v2", None), getattr(imageio, "v3", None)):
        if namespace is not None and hasattr(namespace, "get_writer"):
            namespace.get_writer = patched(namespace.get_writer)


def _pop_custom_options(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """剥离只属于本项目的参数，避免 tyro 把它们当作未知官方参数拒绝。"""
    names = {
        "--headswap-motion-mode",
        "--headswap-pose-gain-pitch",
        "--headswap-pose-gain-yaw",
        "--headswap-pose-gain-roll",
        "--headswap-pose-limit-pitch",
        "--headswap-pose-limit-yaw",
        "--headswap-pose-limit-roll",
        "--headswap-pose-smooth-window",
        "--headswap-motion-report",
    }
    clean = [argv[0]]
    found: dict[str, str] = {}
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in names:
            if i + 1 >= len(argv):
                raise SystemExit(f"{token} 缺少值")
            found[token[2:].replace("-", "_")] = argv[i + 1]
            i += 2
        else:
            clean.append(token)
            i += 1
    return clean, found


def _install_rotation_exp_patch(options: dict[str, str], project_root: Path) -> None:
    mode = options.get("headswap_motion_mode", "all")
    if mode not in {"rotation_exp", "rotation_lip"}:
        return
    sys.path.insert(0, str(project_root / "src"))
    from headswap.motion_control import RotationControl, control_motion_template
    from src.live_portrait_pipeline import LivePortraitPipeline
    from src.live_portrait_wrapper import LivePortraitWrapper

    control = RotationControl(
        pitch_gain=float(options.get("headswap_pose_gain_pitch", "0.65")),
        yaw_gain=float(options.get("headswap_pose_gain_yaw", "0.75")),
        roll_gain=float(options.get("headswap_pose_gain_roll", "0.65")),
        pitch_limit_deg=float(options.get("headswap_pose_limit_pitch", "3.0")),
        yaw_limit_deg=float(options.get("headswap_pose_limit_yaw", "5.0")),
        roll_limit_deg=float(options.get("headswap_pose_limit_roll", "3.0")),
        smooth_window=int(options.get("headswap_pose_smooth_window", "7")),
    )
    control.validate()
    report = Path(options["headswap_motion_report"]).resolve()
    original = LivePortraitPipeline.make_motion_template

    def patched(self, I_lst, c_eyes_lst, c_lip_lst, **kwargs):
        template = original(self, I_lst, c_eyes_lst, c_lip_lst, **kwargs)
        expression_indices = (6, 12, 14, 17, 19, 20) if mode == "rotation_lip" else None
        template, rows = control_motion_template(
            template, control, expression_indices=expression_indices
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.with_suffix(".json").write_text(
            json.dumps(
                {
                    "mode": mode,
                    "control": control.__dict__,
                    "frames": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with report.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return template

    LivePortraitPipeline.make_motion_template = patched

    stitch_rows: list[dict] = []
    original_stitching = LivePortraitWrapper.stitching

    def kp_metrics(tensor, prefix: str) -> dict:
        import cv2
        import numpy as np

        arr = tensor.detach().float().cpu().numpy().reshape(-1, 3)
        xy = arr[:, :2]
        centered = xy - xy.mean(axis=0, keepdims=True)
        rms = float(np.sqrt(np.mean(np.sum(centered * centered, axis=1))))
        hull = cv2.convexHull(xy.astype(np.float32))
        return {
            f"{prefix}_rms_xy": rms,
            f"{prefix}_bbox_w": float(np.ptp(xy[:, 0])),
            f"{prefix}_bbox_h": float(np.ptp(xy[:, 1])),
            f"{prefix}_hull_area": float(cv2.contourArea(hull)),
            f"{prefix}_cx": float(xy[:, 0].mean()),
            f"{prefix}_cy": float(xy[:, 1].mean()),
        }

    def stitching_with_telemetry(self, kp_source, kp_driving):
        before = kp_metrics(kp_driving, "before")
        out = original_stitching(self, kp_source, kp_driving)
        after = kp_metrics(out, "after")
        stitch_rows.append({"frame": len(stitch_rows), **before, **after})
        return out

    LivePortraitWrapper.stitching = stitching_with_telemetry

    def flush_stitch_report() -> None:
        if not stitch_rows:
            return
        path = report.parent / f"{report.name}-stitch.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(stitch_rows[0]))
            writer.writeheader()
            writer.writerows(stitch_rows)

    atexit.register(flush_stitch_report)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python liveportrait_runner.py <inference.py> [参数...]")
    _force_single_thread_encode()
    script = Path(sys.argv[1]).resolve()
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(script.parent))  # inference.py 以 `from src...` 相对仓库导入
    clean, custom = _pop_custom_options([str(script), *sys.argv[2:]])
    sys.argv = clean
    _install_rotation_exp_patch(custom, project_root)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
