from pathlib import Path
import subprocess

import pytest

from dynamix_manager.config import required_env_vars


def test_bootstrap_script_covers_venv_install_steps():
    script = Path("scripts/bootstrap_env.sh")
    assert script.exists()
    text = script.read_text()
    assert 'PYTHON_VERSION="$(python3 -c' in text
    assert 'Python 3.13 or newer is required.' in text
    assert '[ ! -f ".venv/bin/activate" ]' in text
    assert "python3 -m venv .venv" in text
    assert "pip install --upgrade pip" in text
    assert 'pip install --upgrade --editable ".[dev]"' in text


def test_readme_mentions_bootstrap_and_env():
    readme = Path("README.md").read_text()
    assert "bootstrap_env.sh" in readme
    assert ".env" in readme
    assert "Python 3.13+" in readme
    for var_name in required_env_vars():
        assert var_name in readme
    assert "TEAMDYNAMIX_" not in readme


def test_cfo_email_wrapper_runs_from_source_checkout():
    script = Path("create_cfo_email.sh")
    assert script.exists()
    text = script.read_text()
    assert 'export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"' in text
    assert 'PYTHON_BIN=""' in text
    assert 'for candidate in ".venv/bin/python" ".venv/bin/python3"' in text
    assert '"$PYTHON_BIN" -m dynamix_manager.cli generate-cfo-email' in text
    assert "-m dynamix_manager.cli generate-cfo-email" in text


def test_stale_phase_zero_plan_is_not_present():
    assert not Path("docs/superpowers/plans/2026-03-17-local-environment.md").exists()
    assert not Path("docs/superpowers/plans/2026-03-17-phase-0-local-env.md").exists()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_cfo_wrapper_persists_output_and_preserves_exit_status(tmp_path, exit_code):
    script = tmp_path / "create_cfo_email.sh"
    script.write_text(Path("create_cfo_email.sh").read_text())
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/bash\necho "report output"\necho "diagnostic detail" >&2\nexit {exit_code}\n')
    python.chmod(0o700)
    result = subprocess.run(["bash", str(script)], cwd="/", capture_output=True, text=True)
    assert result.returncode == exit_code
    logs = list((tmp_path / "logs").glob("cfo_email_*.log"))
    assert len(logs) == 1
    log = logs[0].read_text()
    assert "report output" in log
    assert "diagnostic detail" in log
    assert f"Exit status: {exit_code}" in log
    assert ("CFO email generation finished." in result.stdout) == (exit_code == 0)
    assert logs[0].stat().st_mode & 0o077 == 0


def test_cfo_wrapper_logs_missing_python(tmp_path):
    script = tmp_path / "create_cfo_email.sh"
    script.write_text(Path("create_cfo_email.sh").read_text())
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode == 1
    log = next((tmp_path / "logs").glob("cfo_email_*.log")).read_text()
    assert "Missing a working .venv Python" in log
    assert "Exit status: 1" in log
