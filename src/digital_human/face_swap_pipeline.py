"""换脸流水线编排 — 授权检查、输入复制、预检、执行、manifest。"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from digital_human.adapters.facefusion import FaceFusionAdapter, FaceSwapProfile
from digital_human.face_swap_config import load_face_swap_home_config, load_face_swap_job_config
from digital_human.face_swap_license import authorize_model


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    for chunk in path.open('rb'):
        h.update(chunk)
    return h.hexdigest()


def run_face_swap(
    home_config_path: Path,
    job_config_path: Path,
) -> Path:
    """端到端换脸流水线入口。

    1. 加载配置
    2. 许可闸门
    3. 复制输入到 F 盘任务目录
    4. 执行换脸
    5. 生成 manifest
    """
    # 1. 配置
    runtime = load_face_swap_home_config(home_config_path)
    job = load_face_swap_job_config(job_config_path)
    usage = job['usage']  # 'research' or 'commercial'
    job_id = job['job_id']
    profile_data = job['profile']

    # 2. 许可闸门
    policy = job.get('policy', {})
    model_name = profile_data.get('face_swapper_model', 'ghost_2_256')
    decision = authorize_model(
        model=model_name,
        usage=usage,
        commercial_allowlist=set(policy.get('commercial_model_allowlist', [])),
        approval_file=Path(policy['license_override_dir']) / f'{model_name}_approved.txt' if policy.get('license_override_dir') else None,
    )
    if not decision.allowed:
        raise PermissionError(f'模型 {model_name} 未通过许可检查: {decision.reason}')

    # 3. 准备任务目录
    job_dir = runtime.jobs_dir / job_id
    input_dir = job_dir / 'input'
    input_dir.mkdir(parents=True, exist_ok=True)

    source_images: list[Path] = []
    for src in job['input']['source_images']:
        src_path = Path(src)
        dst = input_dir / src_path.name
        if not dst.exists():
            shutil.copy2(src_path, dst)
        source_images.append(dst)

    target_video_src = Path(job['input']['target_video'])
    target_video = input_dir / target_video_src.name
    if not target_video.exists():
        shutil.copy2(target_video_src, target_video)

    output_video = Path(job['output']['video'])
    manifest_path = Path(job['output']['manifest'])

    # 4. 执行
    adapter = FaceFusionAdapter(runtime)
    profile = FaceSwapProfile(**profile_data)
    result = adapter.run(source_images, target_video, output_video, profile_data)

    # 5. Manifest
    manifest = {
        'job_id': job_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'input_files': {str(p): _sha256(p) for p in [*source_images, target_video]},
        'output_file': {str(output_video): _sha256(output_video) if output_video.exists() else 'missing'},
        'engine': 'facefusion',
        'engine_version': '3.8.2',
        'profile': profile_data,
        'license_decision': {'model': decision.model, 'allowed': decision.allowed, 'reason': decision.reason},
        'ai_generated': True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

    return output_video
