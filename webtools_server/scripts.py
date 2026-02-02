import pathlib
import sys
import unittest


def _run_module(args: list[str]) -> int:
    import subprocess

    result = subprocess.run([sys.executable, *args], check=False)
    return result.returncode


def run_tests() -> None:
    result = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner()
    if not runner.run(result).wasSuccessful():
        raise SystemExit(1)


def run_coverage() -> None:
    report_dir = pathlib.Path("test-reports") / "coverage"
    report_dir.mkdir(parents=True, exist_ok=True)

    exit_code = _run_module(
        [
            "-m",
            "coverage",
            "run",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ]
    )
    if exit_code != 0:
        raise SystemExit(exit_code)

    exit_code = _run_module(["-m", "coverage", "html", "-d", str(report_dir)])
    if exit_code != 0:
        raise SystemExit(exit_code)


def run_test_html() -> None:
    import HtmlTestRunner

    report_dir = pathlib.Path("test-reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    runner = HtmlTestRunner.HTMLTestRunner(
        output=str(report_dir),
        report_name="unittest",
        report_title="Webtools MCP Test Report",
        combine_reports=True,
    )
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
