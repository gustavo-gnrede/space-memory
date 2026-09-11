import os
import subprocess
import sys


def _clean_env_without(key):
    return {k: v for k, v in os.environ.items() if k != key}


def test_main_fails_fast_without_pepper(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", "import space_memory.main"],
        capture_output=True,
        text=True,
        env=_clean_env_without("SPACE_MEMORY_TOKEN_PEPPER"),
        cwd=str(tmp_path),
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
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr


def test_main_parses_multiple_bootstrap_tokens(tmp_path):
    env = {
        **_clean_env_without("SPACE_MEMORY_TOKEN_PEPPER"),
        "SPACE_MEMORY_TOKEN_PEPPER": "test-pepper",
        "SPACE_MEMORY_DATABASE_URL": f"sqlite:///{tmp_path / 'multi.db'}",
        "SPACE_MEMORY_BOOTSTRAP_TOKENS": (
            '{"spm_a": {"agent_id": "agent-a", "space_id": "space-x"}, '
            '"spm_b": {"agent_id": "agent-b"}}'
        ),
    }
    code = (
        "import space_memory.main as m; import json; "
        "print(json.dumps(m.load_bootstrap_tokens(), sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    assert "agent-a" in result.stdout
    assert "agent-b" in result.stdout
    assert "space-x" in result.stdout
    assert "default" in result.stdout


def test_main_rejects_invalid_bootstrap_json(tmp_path):
    env = {
        **_clean_env_without("SPACE_MEMORY_TOKEN_PEPPER"),
        "SPACE_MEMORY_TOKEN_PEPPER": "test-pepper",
        "SPACE_MEMORY_DATABASE_URL": f"sqlite:///{tmp_path / 'multi.db'}",
        "SPACE_MEMORY_BOOTSTRAP_TOKENS": "not-json",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import space_memory.main"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert result.returncode != 0
    assert "JSON" in result.stderr
