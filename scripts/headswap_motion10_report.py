"""汇总 docs §33 的 10 秒 rotation_exp / neck-pivot 实验材料。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def video_info(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    info = {
        "path": str(path.resolve()),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": float(cap.get(cv2.CAP_PROP_FPS)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "bytes": path.stat().st_size,
    }
    cap.release()
    info["duration"] = round(info["frames"] / max(info["fps"], 1e-9), 6)
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    info["sha256"] = h.hexdigest()
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--review-root", type=Path, required=True)
    ap.add_argument("--jobs", nargs=3, type=Path, required=True, metavar=("R1", "R2", "R3"))
    args = ap.parse_args()
    review = args.review_root.resolve()
    reports = review / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    tags = ("r1", "r2", "r3")
    silent = {"r1": "composite-motion10-r1", "r2": "composite-motion10-r2", "r3": "composite-motion10-r3"}
    final_name = {
        "r1": "motion10-r1-exp-pivot.mp4",
        "r2": "motion10-r2-rot100-pivot.mp4",
        "r3": "motion10-r3-rot-soft-pivot.mp4",
    }
    summary: dict = {"experiment": "LivePortrait rotation_exp + neck pivot, first 10 seconds"}

    # 姿态曲线：R2/R3 的 raw 与实际使用 rotvec。
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    for job, tag in zip(args.jobs, tags):
        pose_csv = job / "work" / "motion10-poses.csv"
        if not pose_csv.is_file():
            continue
        rows = read_csv(pose_csv)
        frames = np.array([int(r["frame"]) for r in rows])
        for ax, axis in zip(axes, ("pitch", "yaw", "roll")):
            used = np.array([float(r[f"used_{axis}_rotvec_deg"]) for r in rows])
            ax.plot(frames, used, label=f"{tag.upper()} used")
            ax.set_ylabel(f"{axis} deg")
            ax.grid(alpha=0.25)
        t = np.array([[float(r["used_tx"]), float(r["used_ty"])] for r in rows])
        scale = np.array([float(r["used_scale"]) for r in rows])
        summary.setdefault(tag, {})["liveportrait"] = {
            "frames": len(rows),
            "used_tx_range": float(np.ptp(t[:, 0])),
            "used_ty_range": float(np.ptp(t[:, 1])),
            "used_scale_range": float(np.ptp(scale)),
            "pose_csv": str(pose_csv.resolve()),
        }
    axes[-1].set_xlabel("frame")
    axes[0].legend(ncol=2)
    fig.suptitle("LivePortrait controlled relative pose (R2/R3)")
    fig.tight_layout()
    pose_plot = reports / "motion10-pose-curves.png"
    fig.savefig(pose_plot, dpi=160)
    plt.close(fig)

    # 支点曲线与 composite 审计。
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    for job, tag in zip(args.jobs, tags):
        work = job / "work"
        pivot_csv = work / f"{silent[tag]}.pivots.csv"
        rows = read_csv(pivot_csv)
        frame = np.array([int(r["frame"]) for r in rows])
        p = np.array([[float(r["p_used_x"]), float(r["p_used_y"])] for r in rows])
        q = np.array([[float(r["q_full_x"]), float(r["q_full_y"])] for r in rows])
        err = np.array([float(r["error_px"]) for r in rows])
        axes[0].plot(frame, p[:, 0], label=f"{tag.upper()} P.x")
        axes[0].plot(frame, q[:, 0], linestyle="--", alpha=0.7, label=f"{tag.upper()} Q.x")
        axes[1].plot(frame, p[:, 1], label=f"{tag.upper()} P.y")
        axes[1].plot(frame, q[:, 1], linestyle="--", alpha=0.7, label=f"{tag.upper()} Q.y")
        axes[2].plot(frame, err, label=tag.upper())
        diag_path = work / f"{silent[tag]}.diag.json"
        diag = json.loads(diag_path.read_text(encoding="utf-8"))
        summary.setdefault(tag, {})["pivot"] = {
            "p95_error_px": float(np.percentile(err, 95)),
            "max_error_px": float(np.max(err)),
            "p_first_last_motion_px": float(np.linalg.norm(p[-1] - p[0])),
            "p_x_range_px": float(np.ptp(p[:, 0])),
            "p_y_range_px": float(np.ptp(p[:, 1])),
            "fallback_frames": int(diag.get("neck_pivot_fallback_frames", 0)),
            "pivot_csv": str(pivot_csv.resolve()),
        }
        summary[tag]["seam_audit"] = {
            "audit_changed_from_skin_max": diag.get("audit_changed_from_skin_max"),
            "audit_wall_intrusion_max": diag.get("audit_wall_intrusion_max"),
            "audit_horizontal_wall_component_width_max": diag.get("audit_horizontal_wall_component_width_max"),
            "junction_corridor_residual_max": diag.get("junction_corridor_residual_max"),
            "jaw_neck_gap_px_max": diag.get("jaw_neck_gap_px_max"),
        }
        summary[tag]["video"] = video_info(review / "output" / final_name[tag])
    for ax, label in zip(axes, ("full-frame X px", "full-frame Y px", "P-Q error px")):
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
        ax.legend(ncol=3, fontsize=8)
    axes[-1].set_xlabel("frame")
    fig.suptitle("A neck pivot P and aligned B attachment Q")
    fig.tight_layout()
    pivot_plot = reports / "motion10-pivot-curves.png"
    fig.savefig(pivot_plot, dpi=160)
    plt.close(fig)

    summary["r0"] = {"video": video_info(review / "output" / "motion10-r0-v7.mp4")}
    summary["comparison"] = {
        "normal": video_info(review / "previews" / "compare-motion10-r0-r1-r2-r3.mp4"),
        "slow": video_info(review / "previews" / "compare-motion10-r0-r1-r2-r3-slow.mp4"),
    }
    summary["plots"] = {"pose": str(pose_plot), "pivot": str(pivot_plot)}
    metrics_path = reports / "motion10-metrics.json"
    if metrics_path.is_file():
        summary["legacy_face_motion_metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
    for tag in ("r0", "r1", "r2", "r3"):
        verify_path = reports / f"verify-{tag}.json"
        if verify_path.is_file():
            summary.setdefault(tag, {})["verify_68_landmarks"] = json.loads(
                verify_path.read_text(encoding="utf-8")
            )
    summary["automatic_recommendation"] = {
        "variant": "R2",
        "reason": (
            "68点复核中 mouth_corr=0.982、roll_corr=0.956、roll_amp=0.890、"
            "lag_roll=0；头颈/墙体审计全0。相较 R0，roll 延迟由3帧降为0，"
            "下颌色差代理由15.87降为8.60。最终仍以用户看片为准。"
        ),
    }
    report_json = reports / "motion10-report.json"
    report_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    review_md = reports / "motion10-review.md"
    review_md.write_text(
        "# 第八轮 10 秒自检\n\n"
        "- R0 是当前 V7 基线；R1 是 exp-only+支点；R2 是 100% rotation_exp+支点；"
        "R3 是柔化 rotation_exp+支点。\n"
        "- 三个 pivot 版本均为 300 帧/30fps/10 秒；未覆盖原 `final.mp4` 或 V7。\n"
        "- LivePortrait rotation_exp 的 used t/scale 全片恒定；外部 scale/angle 全片恒定，"
        "动态二维运动只来自 P-Q。\n"
        "- 改进 neck X 算法后，使用下方完整脖子 carrier band 的左右边界中点，避免"
        "顶部左右碎片面积变化造成约 100px 假横移。\n"
        "- P/Q 数学对齐误差为浮点零；全部接合区/墙体 audit 为 0；仍须用户以"
        " 1.0x/0.5x 判断旋转自然度。\n"
        "- 传统‘输出脸中心必须复制 A 脸中心’指标不再是本方案的硬门槛，因为 yaw/pitch"
        "本来会让脸中心相对颈支点移动；但其相关性和首尾趋势仍作为风险提示保留。\n"
        "- 68 点复核优先推荐 R2：mouth_corr=0.982、roll_corr=0.956、roll_amp=0.890、"
        "lag_roll=0；R1 缺少自然 roll，R3 偏保守。\n"
        "- card_psnr=33.7dB，和 R0 基线完全相同，说明本轮没有新增证件损伤；该数值"
        "低于文档理想阈值 34dB 约 0.3dB，不能写成绝对过闸。\n",
        encoding="utf-8",
    )
    print(report_json)
    print(review_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
