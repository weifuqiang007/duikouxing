"""换脸模型许可闸门 — 商业任务 fail closed。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MODEL_LICENSES: dict[str, str] = {
    "ghost_2_256": "Apache-2.0",
    "hyperswap_1c_256": "ResearchRAIL",
    "inswapper_128_fp16": "Non-Commercial",
}

BLOCKED_FOR_COMMERCIAL: set[str] = {"ResearchRAIL", "Non-Commercial", "Unknown"}


@dataclass(frozen=True)
class LicenseDecision:
    model: str
    declared_license: str
    allowed: bool
    reason: str


def authorize_model(
    model: str,
    usage: str,
    commercial_allowlist: set[str],
    approval_file: Path | None = None,
) -> LicenseDecision:
    declared = MODEL_LICENSES.get(model, "Unknown")
    if usage not in {"research", "commercial"}:
        raise ValueError(f"未知用途: {usage}")

    if usage == "research":
        return LicenseDecision(model, declared, True, "研究/内部测试")

    approved = (
        model in commercial_allowlist
        and approval_file is not None
        and approval_file.is_file()
    )
    if declared in BLOCKED_FOR_COMMERCIAL or not approved:
        return LicenseDecision(
            model,
            declared,
            False,
            "商业任务缺少已审核许可快照和显式 allowlist",
        )
    return LicenseDecision(model, declared, True, "商业许可已人工审核")
