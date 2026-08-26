"""换脸配置加载 — 读取 YAML 构建 FaceFusionRuntime 和 FaceSwapProfile。"""

from __future__ import annotations

from pathlib import Path

import yaml

from digital_human.adapters.facefusion import FaceFusionRuntime, FaceSwapProfile


def load_face_swap_home_config(config_path: Path) -> FaceFusionRuntime:
    """加载本机配置 YAML，返回 FaceFusionRuntime。"""
    with open(config_path, encoding='utf-8') as f:
        data = yaml.safe_load(f)

    rt = data['runtime']
    eng = data['engine']
    providers = tuple(eng.get('execution_providers', ['cuda']))

    return FaceFusionRuntime(
        python=Path(rt['facefusion_python']),
        repo=Path(rt['facefusion_repo']),
        temp_dir=Path(rt['temp_dir']),
        jobs_dir=Path(rt['jobs_dir']),
        runtime_root=Path(rt['root']),
        execution_providers=providers,
    )


def load_face_swap_job_config(config_path: Path) -> dict:
    """加载任务配置 YAML，返回原始字典供流水线使用。"""
    with open(config_path, encoding='utf-8') as f:
        return yaml.safe_load(f)
