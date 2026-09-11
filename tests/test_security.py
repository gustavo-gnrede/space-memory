import os
import subprocess
import sys


def _clean_env_without(key):
    return {k: v for k, v in os.environ.items() if k != key}


def test_main_fails_fast_without_pepper():
    result = subprocess.run(
        [sys.executable, "-c", "import space_memory.main"],
        capture_output=True,
        text=True,
        env=_clean_env_without("SPACE_MEMORY_TOKEN_PEPPER"),
    )
    assert result.returncode != 0
    assert "SPACE_MEMORY_TOKEN_PEPPER" in result.stderr


def test_main_imports_with_pepper_set(tmp_path):
    env = {
        **_clean_env_without("SPACE_MEMORY_TOKEN_PEPPER"),
        "SPACE_MEMORY_TOKEN_PEPPER": "test-pepper",
        "SPACE_MEMORY_DATABASE_URL": f"sqlite:///{tmp_path / 'main.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import space_memory.main"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
