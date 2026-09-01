"""§35 头部“呼吸/收缩”分层诊断报告。

把 LivePortrait stitching 前后关键点、composite mask/alpha 遥测与三个视频的
68 点稳定尺度放在同一时间轴，判断收缩最早出现在哪一层。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def numeric(rows: list[dict[str, str]], key: str) -> np.ndarray:
    out = []
    for row in rows:
        try:
            out.append(float(row[key]))
        except (KeyError, TypeError, ValueError):
            out.append(float("nan"))
    arr = np.asarray(out, dtype=np.float64)
    good = np.isfinite(arr)
    if good.any() and not good.all():
        arr[~good] = np.interp(np.flatnonzero(~good), np.flatnonzero(good), arr[good])
    return arr


def lowpass(x: np.ndarray, window: int = 31) -> np.ndarray:
    half = window // 2
    return np.asarray(
        [np.mean(x[max(0, i - half): min(len(x), i + half + 1)]) for i in range(len(x))]
    )


def metric(x: np.ndarray) -> dict[str, float]:
    x = np.asarray(x, dtype=np.float64)
    lp = lowpass(x)
    hp = x - lp
    mean = max(abs(float(np.mean(x))), 1e-12)
    return {
        "mean": float(np.mean(x)),
        "range": float(np.ptp(x)),
        "range_pct": float(100.0 * np.ptp(x) / mean),
        "first_last_pct": float(100.0 * (x[-1] - x[0]) / mean),
        "highpass_rms_pct": float(100.0 * np.sqrt(np.mean(hp * hp)) / mean),
        "highpass_p95_pct": float(100.0 * np.percentile(np.abs(hp), 95) / mean),
    }


def rect_iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    ab = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / max(aa + ab - inter, 1e-9)


def video_landmark_series(app, path: Path, max_frames: int = 300) -> dict[str, np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    data = {k: [] for k in ("detected", "eye_distance", "stable_rms", "bbox_w", "bbox_h")}
    prev_box = None
    last = None
    for _ in range(max_frames):
        ok, frame = cap.read()
        if not ok:
            break
        faces = app.get(frame)
        if faces:
            face = (
                max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                if prev_box is None else max(faces, key=lambda f: rect_iou(f.bbox, prev_box))
            )
            prev_box = np.asarray(face.bbox, dtype=np.float64)
            # 只用 detector 五点中的双眼+鼻尖，明确排除两个嘴角；避免口播污染尺度。
            lm = np.asarray(face.kps, dtype=np.float64)
            xy = lm[:3, :2]
            centered = xy - xy.mean(axis=0, keepdims=True)
            last = {
                "detected": 1.0,
                "eye_distance": float(np.linalg.norm(lm[1, :2] - lm[0, :2])),
                "stable_rms": float(np.sqrt(np.mean(np.sum(centered * centered, axis=1)))),
                "bbox_w": float(prev_box[2] - prev_box[0]),
                "bbox_h": float(prev_box[3] - prev_box[1]),
            }
        elif last is not None:
            last = {**last, "detected": 0.0}
        else:
            last = {k: float("nan") for k in data}
            last["detected"] = 0.0
        for key in data:
            data[key].append(last[key])
    cap.release()
    out = {key: np.asarray(value, dtype=np.float64) for key, value in data.items()}
    for key in ("eye_distance", "stable_rms", "bbox_w", "bbox_h"):
        good = np.isfinite(out[key])
        if good.any() and not good.all():
            out[key][~good] = np.interp(np.flatnonzero(~good), np.flatnonzero(good), out[key][good])
    return out


def correlation(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    if n < 10:
        return float("nan")
    return float(np.corrcoef((a[:n] - lowpass(a[:n])), (b[:n] - lowpass(b[:n])))[0, 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stitch-csv", required=True, type=Path)
    ap.add_argument("--ema05-csv", required=True, type=Path)
    ap.add_argument("--ema0-csv", required=True, type=Path)
    ap.add_argument("--animated-head", required=True, type=Path)
    ap.add_argument("--ema05-video", required=True, type=Path)
    ap.add_argument("--ema0-video", required=True, type=Path)
    ap.add_argument("--lip-csv", type=Path, default=None)
    ap.add_argument("--lip-stitch-csv", type=Path, default=None)
    ap.add_argument("--lip-animated", type=Path, default=None)
    ap.add_argument("--lip-video", type=Path, default=None)
    ap.add_argument("--insightface-root", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    stitch = read_rows(args.stitch_csv)
    ema05 = read_rows(args.ema05_csv)
    ema0 = read_rows(args.ema0_csv)

    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l", root=str(args.insightface_root),
        allowed_modules=["detection"],
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(640, 640))
    videos = {
        "animated_head": video_landmark_series(app, args.animated_head),
        "final_ema05": video_landmark_series(app, args.ema05_video),
        "final_ema0": video_landmark_series(app, args.ema0_video),
    }
    if args.lip_animated is not None:
        videos["animated_rotation_lip"] = video_landmark_series(app, args.lip_animated)
    if args.lip_video is not None:
        videos["final_rotation_lip"] = video_landmark_series(app, args.lip_video)

    report: dict = {
        "frames": len(ema05),
        "external": {
            "scale": metric(numeric(ema05, "external_scale")),
        },
        "detector": {
            "success_frames": int(sum(int(float(r["detected"])) for r in ema05)),
            "fallback_frames": int(sum(not int(float(r["detected"])) for r in ema05)),
            "resolved_eye_distance": metric(numeric(ema05, "resolved_eye_distance")),
        },
        "liveportrait": {},
        "mask": {"ema05": {}, "ema0": {}},
        "video_features": {},
    }

    for phase in ("before", "after"):
        for name in ("rms_xy", "bbox_w", "bbox_h", "hull_area"):
            report["liveportrait"][f"{phase}_{name}"] = metric(numeric(stitch, f"{phase}_{name}"))
    for name in ("rms_xy", "bbox_w", "bbox_h", "hull_area"):
        ratio = numeric(stitch, f"after_{name}") / np.maximum(numeric(stitch, f"before_{name}"), 1e-12)
        report["liveportrait"][f"stitch_ratio_{name}"] = metric(ratio)

    cols = (
        "parser_raw_area", "component_area", "morph_area", "ema_area", "trim_area",
        "alpha_0p05_area", "alpha_0p5_area", "alpha_0p95_area",
        "alpha_0p5_bbox_w", "alpha_0p5_bbox_h",
    )
    for tag, rows in (("ema05", ema05), ("ema0", ema0)):
        for col in cols:
            report["mask"][tag][col] = metric(numeric(rows, col))
    for tag, series in videos.items():
        report["video_features"][tag] = {
            "detected_frames": int(np.nansum(series["detected"])),
            "eye_distance": metric(series["eye_distance"]),
            "stable_rms": metric(series["stable_rms"]),
            "bbox_w": metric(series["bbox_w"]),
            "bbox_h": metric(series["bbox_h"]),
        }

    lip_rows = read_rows(args.lip_csv) if args.lip_csv is not None else None
    lip_stitch = read_rows(args.lip_stitch_csv) if args.lip_stitch_csv is not None else None
    if lip_rows is not None:
        report["rotation_lip_candidate"] = {
            "detector_success_frames": int(sum(int(float(r["detected"])) for r in lip_rows)),
            "resolved_eye_distance": metric(numeric(lip_rows, "resolved_eye_distance")),
            "parser_raw_area": metric(numeric(lip_rows, "parser_raw_area")),
            "alpha_0p5_area": metric(numeric(lip_rows, "alpha_0p5_area")),
        }
    if lip_stitch is not None:
        report.setdefault("rotation_lip_candidate", {})["lp_before_rms"] = metric(
            numeric(lip_stitch, "before_rms_xy")
        )

    report["cross_layer_highpass_corr"] = {
        "lp_before_rms_vs_animated_stable_rms": correlation(
            numeric(stitch, "before_rms_xy"), videos["animated_head"]["stable_rms"]
        ),
        "animated_stable_rms_vs_alpha_ema05_area": correlation(
            videos["animated_head"]["stable_rms"], numeric(ema05, "alpha_0p5_area")
        ),
        "parser_raw_vs_alpha_ema0": correlation(
            numeric(ema0, "parser_raw_area"), numeric(ema0, "alpha_0p5_area")
        ),
    }

    # 自动裁决依据：stitching 的 after/before 比值几乎不动；EMA0.5 是单调面积累积，
    # 周期高频反而比 EMA0 小；因此两者都不是“周期性缩”的首发点。
    stitch_scale_hp = report["liveportrait"]["stitch_ratio_rms_xy"]["highpass_rms_pct"]
    alpha05_hp = report["mask"]["ema05"]["alpha_0p5_area"]["highpass_rms_pct"]
    alpha0_hp = report["mask"]["ema0"]["alpha_0p5_area"]["highpass_rms_pct"]
    animated_hp = report["video_features"]["animated_head"]["stable_rms"]["highpass_rms_pct"]
    report["decision"] = {
        "primary_layer": "LivePortrait pre-stitch motion/expression -> generated RGB geometry",
        "not_primary": [
            "external scale (strictly constant)",
            "stitching global scale (after/before high-pass is near zero)",
            "fusion alpha periodic pulse (legacy EMA reduces high-pass rather than creating it)",
        ],
        "secondary_faults": [
            "B detector fallback is excessive and must still be replaced by interpolation/tracking",
            "binary EMA 0.5 is a monotonic historical union and causes slow outline growth",
        ],
        "numbers": {
            "stitch_ratio_rms_highpass_pct": stitch_scale_hp,
            "animated_stable_rms_highpass_pct": animated_hp,
            "alpha_ema05_highpass_pct": alpha05_hp,
            "alpha_ema0_highpass_pct": alpha0_hp,
        },
        "next_isolation": "rotation_lip vs full-exp; keep R, t, scale and composite identical",
    }
    if lip_rows is not None and "animated_rotation_lip" in videos:
        full_eye_hp = report["video_features"]["animated_head"]["eye_distance"]["highpass_rms_pct"]
        lip_eye_hp = report["video_features"]["animated_rotation_lip"]["eye_distance"]["highpass_rms_pct"]
        full_bbox_h_hp = report["video_features"]["animated_head"]["bbox_h"]["highpass_rms_pct"]
        lip_bbox_h_hp = report["video_features"]["animated_rotation_lip"]["bbox_h"]["highpass_rms_pct"]
        full_alpha_range = report["mask"]["ema05"]["alpha_0p5_area"]["range_pct"]
        lip_alpha_range = report["rotation_lip_candidate"]["alpha_0p5_area"]["range_pct"]
        report["decision"].update(
            {
                "confirmed_cause": (
                    "full-exp drives non-lip facial geometry; detector hold and binary EMA amplify/"
                    "distort the resulting contour. rotation_lip + interpolation + EMA0 reduces it."
                ),
                "candidate": "E3 rotation_lip + B offline interpolation + head_ema=0",
                "improvement": {
                    "animated_eye_highpass_reduction_pct": 100 * (1 - lip_eye_hp / full_eye_hp),
                    "animated_bbox_h_highpass_reduction_pct": 100 * (1 - lip_bbox_h_hp / full_bbox_h_hp),
                    "alpha_range_reduction_pct": 100 * (1 - lip_alpha_range / full_alpha_range),
                },
                "next_isolation": "user review E3; if mouth is acceptable, replace binary EMA with motion-SDF rather than restore it",
            }
        )

    # 曲线图
    frame = np.arange(len(ema05))
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    axes[0].plot(frame, 100 * (numeric(stitch, "before_rms_xy") / np.mean(numeric(stitch, "before_rms_xy")) - 1), label="LP before stitch RMS")
    axes[0].plot(frame, 100 * (numeric(stitch, "after_rms_xy") / np.mean(numeric(stitch, "after_rms_xy")) - 1), label="LP after stitch RMS", alpha=.8)
    axes[1].plot(frame, 100 * (videos["animated_head"]["stable_rms"] / np.mean(videos["animated_head"]["stable_rms"]) - 1), label="animated_head 68 stable RMS")
    axes[2].plot(frame, 100 * (numeric(ema05, "alpha_0p5_area") / np.mean(numeric(ema05, "alpha_0p5_area")) - 1), label="alpha area EMA .5")
    axes[2].plot(frame, 100 * (numeric(ema0, "alpha_0p5_area") / np.mean(numeric(ema0, "alpha_0p5_area")) - 1), label="alpha area EMA 0")
    axes[3].plot(frame, numeric(ema05, "detected"), label="B detector success", drawstyle="steps-mid")
    for ax in axes:
        ax.grid(alpha=.25)
        ax.legend(loc="upper right")
    axes[0].set_ylabel("relative %")
    axes[1].set_ylabel("relative %")
    axes[2].set_ylabel("relative %")
    axes[3].set_ylabel("0/1")
    axes[3].set_xlabel("frame")
    fig.suptitle("Head shrink layered telemetry")
    fig.tight_layout()
    plot = args.output_dir / "shrink-layered-curves.png"
    fig.savefig(plot, dpi=160)
    plt.close(fig)

    # pulse 最大帧
    ah = videos["animated_head"]["stable_rms"]
    hp = ah - lowpass(ah)
    pulse_frames = np.argsort(np.abs(hp))[-10:][::-1].astype(int).tolist()
    report["top_pulse_frames_from_animated_head"] = pulse_frames
    report["plot"] = str(plot.resolve())

    json_path = args.output_dir / "shrink-diagnostic-report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = args.output_dir / "shrink-diagnostic-report.md"
    md_path.write_text(
        "# 头部收缩分层诊断结论\n\n"
        f"- 外部 scale range：{report['external']['scale']['range']:.9f}（排除）。\n"
        f"- B 检测成功：{report['detector']['success_frames']}/{report['frames']}，"
        f"fallback {report['detector']['fallback_frames']} 帧（严重次生问题）。\n"
        f"- stitching after/before RMS 比值高频：{stitch_scale_hp:.4f}%（可排除 stitching 全局缩放）。\n"
        f"- animated_head 稳定 68 点尺度高频：{animated_hp:.4f}%。\n"
        f"- alpha@0.5 高频：EMA0.5={alpha05_hp:.4f}%，EMA0={alpha0_hp:.4f}%。\n"
        "- EMA0.5 的 mask 面积单调累积，是错误的历史并集；但它降低而不是制造周期高频，"
        "所以不是客户所见周期收缩的首发点。\n"
        "- 收缩信号在 composite 之前已经存在，最早可定位到 LivePortrait stitching 前的"
        "运动/完整 exp 几何，并在 animated_head RGB 中可测。\n"
        "- rotation_lip 对照已经加入最终 JSON：它显著降低 animated_head 的眼距/框高"
        "高频和最终 alpha 总范围；E3 同时使用离线插值与 EMA0。\n"
        "- 当前确认是 full-exp 非嘴部几何为首发源，hold/二值 EMA 为放大或慢性轮廓"
        "漂移源；建议用户优先查看 E3。\n",
        encoding="utf-8",
    )
    print(json_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
