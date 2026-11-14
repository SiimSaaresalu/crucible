from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from crucible.core.scoring import Score, load_scoring_config


class Cfg(BaseModel):
    weight: float = Field(ge=0, le=1)
    names: list[str]


def test_score_defaults() -> None:
    s = Score(total=0.5)
    assert s.breakdown == {}
    assert s.flags == {}


def test_loads_and_validates(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("weight: 0.5\nnames: [a, b]\n")
    cfg, digest = load_scoring_config(p, Cfg)
    assert cfg == Cfg(weight=0.5, names=["a", "b"])
    assert len(digest) == 64


def test_hash_ignores_formatting(tmp_path: Path) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("weight: 0.5\nnames: [a, b]\n")
    b.write_text("# comment\nnames:\n  - a\n  - b\n\nweight:   0.50\n")
    assert load_scoring_config(a, Cfg)[1] == load_scoring_config(b, Cfg)[1]


def test_hash_changes_with_values(tmp_path: Path) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("weight: 0.5\nnames: [a]\n")
    b.write_text("weight: 0.6\nnames: [a]\n")
    assert load_scoring_config(a, Cfg)[1] != load_scoring_config(b, Cfg)[1]


def test_invalid_config_raises_clear_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("weight: 2\nnames: [a]\n")
    with pytest.raises(ValueError, match=r"(?s)Invalid scoring config .*bad\.yaml.*weight"):
        load_scoring_config(p, Cfg)


def test_invalid_yaml_raises_clear_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("weight: [unclosed\n")
    with pytest.raises(ValueError, match="Invalid YAML"):
        load_scoring_config(p, Cfg)
