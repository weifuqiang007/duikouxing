from __future__ import annotations

import sys
from pathlib import Path

from digital_human.utils.annotate import select_mouth_roi, select_polygon_points
from .id_card import preview_id_card, replace_id_card_in_video
from .composite import composite_video, preview_roi
from .config import ConfigurationError, load_id_card_config, load_job_config, load_local_config
from .pipeline import Pipeline
from .ffmpeg import mux_audio
from .process import CommandError
from .utils.cli_function import (
    build_parser,
    configure_project_local_storage,
    resolve_local_config_path,
    run_doctor,
    write_back_id_card_corners,
    write_back_mouth_roi,
    write_back_protect_polygon,
)


def main(argv: list[str] | None = None) -> int:
    configure_project_local_storage()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return run_doctor(resolve_local_config_path(args.config, args.profile))
        job = load_job_config(args.job.resolve())
        # 仅仅针对facefusion才需要
        if args.command == "annotate-roi":
            roi = select_mouth_roi(job.source_video, job.mouth_roi, args.at_seconds)
            if roi is None:
                print("已取消，配置未修改")
                return 1
            write_back_mouth_roi(args.job.resolve(), roi)
            preview_roi(job.source_video, roi, args.output.resolve())
            # 输出保持 ASCII：conda run 在 GBK 控制台回显中文会崩溃
            print(
                f"mouth_roi saved to {args.job}: "
                f"center=({roi.center_x}, {roi.center_y}) "
                f"size=({roi.width} x {roi.height})"
            )
            print(f"review image: {args.output.resolve()}")
            return 0
        if args.command == "preview-roi":
            preview_roi(job.source_video, job.mouth_roi, args.output.resolve())
            print(args.output.resolve())
            return 0
        if args.command == "run":
            local = load_local_config(resolve_local_config_path(args.config, args.profile))
            #todo 这里写的也不太好。这个流程太长了。应该将这个流程写在一个pipe中，或者是一个mq中。状态要监控，结果要回调。方便查看问题所在。
            # 而且也没办法将这个过程实时的返回给项目。我还是想将这个工工程封装成一个SDK供外部使用。
            output = Pipeline(local, job, force=args.force).run()
            print(output)
            return 0
        if args.command == "refine":
            local = load_local_config(resolve_local_config_path(args.config, args.profile))
            root = local.jobs_root / job.job_id
            work = root / "work"
            base = work / "base_duration_matched.mp4"
            generated = work / "musetalk_result.mp4"
            audio = work / "target_normalized.wav"
            for required in (base, generated, audio):
                if not required.is_file():
                    raise RuntimeError(f"缺少已有阶段文件，无法 refine: {required}")
            silent = work / "composite_refined.mkv"
            composite_video(
                base,
                generated,
                silent,
                job.mouth_roi,
                int(job.video.get("fps", 25)),
                job.composite,
            )
            output = (
                args.output.resolve()
                if args.output
                else root / "output" / "final-refined.mp4"
            )
            mux_audio(local.ffmpeg, silent, audio, output)
            print(output)
            return 0
        if args.command == "annotate-id-card":
            job = load_job_config(args.job.resolve())
            points = select_polygon_points(
                job.source_video, args.at_seconds,
                title="ID Card Corners (TL, TR, BR, BL)",
                expected_points=4,
            )
            if points is None:
                print("cancelled")
                return 1
            write_back_id_card_corners(args.job.resolve(), points)
            id_cfg = load_id_card_config(args.job.resolve())
            if id_cfg:
                preview_id_card(job.source_video, id_cfg, args.output.resolve())
            print(f"corners saved: {len(points)} points -> {args.job}")
            return 0
        if args.command == "annotate-id-card-protect":
            job = load_job_config(args.job.resolve())
            points = select_polygon_points(
                job.source_video, args.at_seconds,
                title=f"Protect polygon: {args.name}",
            )
            if points is None or len(points) < 3:
                print("cancelled")
                return 1
            write_back_protect_polygon(args.job.resolve(), args.name, points)
            print(f"protect '{args.name}' saved: {len(points)} points")
            return 0
        if args.command == "replace-id-card":
            id_cfg = load_id_card_config(args.job.resolve())
            if id_cfg is None:
                print("ERROR: id_card_replacement not enabled", file=sys.stderr)
                return 2
            output = replace_id_card_in_video(id_cfg)
            print(output)
            return 0
    except (ConfigurationError, CommandError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
