from pathlib import Path

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


def test_stale_phase_zero_plan_is_not_present():
    assert not Path("docs/superpowers/plans/2026-03-17-local-environment.md").exists()
    assert not Path("docs/superpowers/plans/2026-03-17-phase-0-local-env.md").exists()
