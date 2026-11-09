"""Objective file inspection that feeds the verifier."""

from __future__ import annotations

from app.config import ROOT
from app.inspect import inspect_path


def sample(name: str) -> dict:
    return inspect_path(ROOT / "samples" / name, name)


def test_final_logo_metadata():
    info = sample("logo-final.svg")

    assert info["valid"] is True
    assert info["ext"] == "svg"
    assert info["width"] == 1_200
    assert info["height"] == 400
    assert info["color_count"] == 3
    assert info["has_full_bleed_background"] is False
    assert info["placeholder_terms"] == []
    assert info["text"]
    assert info["sha256"]


def test_draft_logo_is_an_objective_failure():
    info = sample("logo-draft.svg")

    assert info["valid"] is True
    assert info["width"] == 400
    assert info["height"] == 200
    assert info["color_count"] == 11
    assert info["has_full_bleed_background"] is True
    assert "placeholder" in info["placeholder_terms"]
    assert "lorem ipsum" in info["placeholder_terms"]


def test_brand_guide_is_readable_text():
    info = sample("brand-guide.md")

    assert info["valid"] is True
    assert info["ext"] == "md"
    assert info["word_count"] > 50
    assert "licence" in info["text"].lower() or "license" in info["text"].lower()


def test_inspection_flags_a_missing_file(tmp_path):
    missing = tmp_path / "nope.svg"

    info = inspect_path(missing, "nope.svg")

    assert info["valid"] is False
    assert info["error"]
