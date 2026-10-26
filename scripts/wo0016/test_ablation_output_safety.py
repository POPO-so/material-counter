"""A diagnostic run must never overwrite evidence or write into the checkout."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablate_split_datasets as ablation


def test_ablation_rejects_any_existing_output_directory(tmp_path):
    with pytest.raises(FileExistsError):
        ablation.run(SimpleNamespace(output_dir=str(tmp_path)))


def test_ablation_rejects_output_in_repository_before_mkdir(monkeypatch):
    def forbidden_mkdir(*args, **kwargs):
        raise AssertionError("no directory may be created before rejecting a repository output")

    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    with pytest.raises(ValueError, match="outside"):
        ablation.run(SimpleNamespace(output_dir=str(ablation.REPO_ROOT / "data/results/forbidden-l3-output")))
