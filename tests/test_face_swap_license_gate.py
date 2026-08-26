from pathlib import Path

import pytest

from digital_human.face_swap_license import (
    BLOCKED_FOR_COMMERCIAL,
    LicenseDecision,
    authorize_model,
)


class TestAuthorizeModel:
    def test_research_always_allowed(self) -> None:
        for model in ("ghost_2_256", "hyperswap_1c_256", "inswapper_128_fp16", "unknown_model"):
            d = authorize_model(model, "research", set())
            assert d.allowed is True
            assert "研究" in d.reason

    def test_commercial_blocked_without_allowlist(self, tmp_path: Path) -> None:
        d = authorize_model("ghost_2_256", "commercial", set())
        assert d.allowed is False
        assert "缺少" in d.reason

    def test_commercial_blocked_for_researchrail(self, tmp_path: Path) -> None:
        approval = tmp_path / "approval.txt"
        approval.write_text("approved")
        d = authorize_model("hyperswap_1c_256", "commercial", {"hyperswap_1c_256"}, approval)
        assert d.allowed is False
        assert d.declared_license == "ResearchRAIL"

    def test_commercial_blocked_for_noncommercial(self, tmp_path: Path) -> None:
        approval = tmp_path / "approval.txt"
        approval.write_text("approved")
        d = authorize_model("inswapper_128_fp16", "commercial", {"inswapper_128_fp16"}, approval)
        assert d.allowed is False

    def test_commercial_allowed_with_allowlist_and_file(self, tmp_path: Path) -> None:
        approval = tmp_path / "approval.txt"
        approval.write_text("approved")
        d = authorize_model("ghost_2_256", "commercial", {"ghost_2_256"}, approval)
        assert d.allowed is True
        assert d.reason == "商业许可已人工审核"

    def test_commercial_blocked_if_approval_file_missing(self) -> None:
        d = authorize_model("ghost_2_256", "commercial", {"ghost_2_256"}, None)
        assert d.allowed is False

    def test_unknown_usage_raises(self) -> None:
        with pytest.raises(ValueError, match="未知用途"):
            authorize_model("ghost_2_256", "illegal", set())

    def test_unknown_model_gets_unknown_license(self) -> None:
        d = authorize_model("no_such_model", "research", set())
        assert d.declared_license == "Unknown"
