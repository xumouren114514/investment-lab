import importlib.util
from pathlib import Path
import subprocess
import sys


PROJECT = Path(__file__).resolve().parents[1]


def test_explicit_python_syntax_checks_scripts_and_tests_without_execution(tmp_path):
    helper = PROJECT / "scripts/check_python_syntax.py"
    script = tmp_path / "a script.py"
    test = tmp_path / "test_invalid.py"
    side_effect = tmp_path / "must_not_exist.txt"
    script.write_text(f"from pathlib import Path\nPath({str(side_effect)!r}).write_text('executed')\n", encoding="utf-8")
    test.write_text("def missing_colon()\n    pass\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(helper), str(script), str(test)], capture_output=True, text=True)
    assert result.returncode == 1
    assert str(test) in result.stdout
    assert not side_effect.exists()
    assert not (tmp_path / "__pycache__").exists()
    test.write_text("def valid():\n    return '中文'\n", encoding="utf-8")
    passed = subprocess.run([sys.executable, str(helper), str(script), str(test)], capture_output=True, text=True)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert not side_effect.exists()


def test_daily_task_rejects_other_timezone_before_registration():
    """Only extract and run the timezone guard, never register a real task."""
    source = (PROJECT / "scripts/register_daily_task.ps1").read_text(encoding="utf-8-sig")
    guard = source.split("$machineTimeZone =", 1)[1].split("if (Get-ScheduledTask", 1)[0]
    guard = "$machineTimeZone =" + guard
    command = "function Get-TimeZone { [pscustomobject]@{ Id = 'UTC' } }; " + guard
    denied = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True)
    assert denied.returncode != 0
    allowed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command.replace("Id = 'UTC'", "Id = 'China Standard Time'")], capture_output=True)
    assert allowed.returncode == 0
    assert "Set-TimeZone" not in source
