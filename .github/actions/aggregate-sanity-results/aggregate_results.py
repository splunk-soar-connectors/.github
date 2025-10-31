#!/usr/bin/env python3
"""
Aggregate sanity test results from multiple matrix job artifacts.
Creates a comprehensive GitHub step summary with test results.
"""

import argparse
import os
import re
import glob
import json
from typing import NamedTuple, Optional

# Constants
LOG_FILE_CLEAN = "pytest-output.log"
LOG_FILE_RAW = "pytest-output-raw.log"
STATUS_PASS = "✅ PASS"
STATUS_FAIL = "❌ FAIL"
STATUS_NO_LOG = "⚠️ NO LOG"
STATUS_ERROR = "⚠️ ERROR"

# Regex patterns (compiled for reuse)
EXECUTION_PATTERN = re.compile(r"(suite/[^\n]+::test_\w+\[([^\]]+)\])\s*\n-+ live log call -+")
FAILURES_PATTERN = re.compile(r"_+\s+TestApp\d*\.test_\w+\[([^\]]+)\]\s+_+")
ERRORS_PATTERN = re.compile(r"_+\s*ERROR at setup of TestApp\d*\.test_\w+\[([^\]]+)\]\s+_+")
TEST_PARAM_PATTERN = re.compile(r"\[([^\]]+)\]")
FILE_LOCATION_PATTERN = re.compile(r'File "([^"]+)", line (\d+)')

# Common strings
NO_APP_ERROR = "No application error captured"
SETUP_ERROR = "Setup error - test did not execute"
CONNECTIVITY_FAILED_1 = "TestConnectivityFailed"
CONNECTIVITY_FAILED_2 = "Test connectivity failed"
CONNECTOR_IMPORT_FAILED = "Could not initialize connector"


class TestResult(NamedTuple):
    """Test result data for a single matrix job."""

    version: str
    status: str
    passed: int
    failed: int
    errors: int
    time: str


class FailedTest(NamedTuple):
    """Details of a failed test."""

    test_name: str
    test_parameter: str
    app_error: str  # Live application error (SQL, JSON, etc)
    python_error: str  # Python traceback/assertion
    file_location: str
    error_message: str  # Concise error summary for display


def _find_log_file(log_file_path: str) -> Optional[str]:
    """Find the log file, preferring clean logs over raw logs."""
    if os.path.exists(log_file_path):
        return log_file_path

    raw_log_path = log_file_path.replace(LOG_FILE_CLEAN, LOG_FILE_RAW)
    if os.path.exists(raw_log_path):
        return raw_log_path

    return None


def _parse_summary_line(line: str) -> tuple[int, int, int, str]:
    """Parse pytest summary line and extract counts and time."""
    passed = failed = errors = 0
    time = "N/A"

    passed_match = re.search(r"(\d+) passed", line)
    failed_match = re.search(r"(\d+) failed", line)
    error_match = re.search(r"(\d+) error", line)

    if passed_match:
        passed = int(passed_match.group(1))
    if failed_match:
        failed = int(failed_match.group(1))
    if error_match:
        errors = int(error_match.group(1))

    time_match = re.search(r"in ([\d:.]+)s", line)
    if time_match:
        time = f"{time_match.group(1)}s"

    return passed, failed, errors, time


def _is_summary_line(line: str) -> bool:
    """Check if a line is a pytest summary line.

    Matches lines like:
    - == 2 failed, 23 passed in 227.61s ==
    - = 37 failed, 3 passed, 2 skipped in 157.69s =
    - == 25 errors, 25 rerun in 6.91s ==
    """
    stripped = line.strip()
    has_keyword = any(kw in line for kw in ("failed", "passed", "error"))
    has_timing = "in " in line and "s" in line
    # Accept both single and double equals
    return stripped.startswith("=") and has_keyword and has_timing


def _extract_test_parameter(line: str) -> Optional[str]:
    """Extract test parameter from test name line.

    Example: test_action[pytest/snowflake-disable_user_1_000] -> pytest/snowflake-disable_user_1_000
    """
    match = TEST_PARAM_PATTERN.search(line)
    return match.group(1) if match else None


def _extract_app_error_message(json_str: str) -> str:
    """Extract error message from Phantom JSON response."""
    try:
        data = json.loads(json_str)
        return data.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def _extract_live_error(content: str, start_pos: int) -> str:
    """Extract live application error starting from position.

    Returns the complete error section including all log messages and JSON.
    """
    # Find the nearest boundary marker
    stop_markers = [
        ("::test_action[", start_pos),
        ("FAILED", start_pos),
        ("PASSED", start_pos),
    ]

    boundaries = [content.find(marker, pos) for marker, pos in stop_markers]

    # Use the first valid boundary found, or fallback to 5000 chars
    next_boundary = next((b for b in boundaries if b != -1), start_pos + 5000)
    error_section = content[start_pos:next_boundary]

    # Trim at additional stop markers
    for marker in ["RERUN", "------- Captured", "========"]:
        marker_pos = error_section.find(marker)
        if marker_pos != -1:
            error_section = error_section[:marker_pos]

    # Clean up whitespace while preserving structure
    cleaned_lines = [line.rstrip() for line in error_section.strip().split("\n") if line.strip()]

    return "\n".join(cleaned_lines)


def _extract_python_traceback(content: str, failure_pos: int) -> tuple[str, str]:
    """Extract Python traceback and file location from FAILURES section.

    Returns: (complete_traceback, file_location)
    """
    lines = content[failure_pos:].split("\n")
    traceback_lines = []
    file_location = ""

    # Stop conditions
    stop_keywords = ["Captured log", "Captured stdout", "Captured stderr", "short test summary"]

    for i, line in enumerate(lines):
        # Stop at captured log section or summary
        if any(keyword in line for keyword in stop_keywords):
            break

        # Stop at next test failure or error
        if i > 0 and line.strip().startswith("_") and "TestApp" in line:
            break

        # Extract file location from first traceback line
        if not file_location and 'File "' in line and ", line " in line:
            match = FILE_LOCATION_PATTERN.search(line)
            if match:
                file_path = match.group(1)
                line_num = match.group(2)
                # Shorten path for readability
                if "/actions-runner/_work/" in file_path:
                    file_path = file_path.split("/actions-runner/_work/")[1]
                file_location = f"{file_path}:{line_num}"

        # Collect all traceback lines
        if line.strip():
            traceback_lines.append(line.rstrip())

    return "\n".join(traceback_lines), file_location


def extract_failed_test_details(log_file_path: str) -> list[FailedTest]:
    """Extract detailed information about failed tests.

    Two-phase extraction:
    1. Parse execution section for live application errors
    2. Parse FAILURES section for Python tracebacks
    3. Match them using test parameter
    """
    log_file = _find_log_file(log_file_path)
    if not log_file:
        return []

    try:
        with open(log_file, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"  ERROR: Could not read log file {log_file}: {e}")
        return []

    failed_tests = []

    # Phase 1: Find all FAILED tests in execution section
    live_errors = {}

    for match in EXECUTION_PATTERN.finditer(content):
        test_parameter = match.group(2)
        start_pos = match.end()

        # Check if this test actually failed
        # Look ahead to find FAILED or PASSED
        next_test_pos = content.find("::test_action[", start_pos)
        result_section = content[
            start_pos : next_test_pos if next_test_pos > 0 else start_pos + 5000
        ]

        if "FAILED" in result_section or "RERUN" in result_section:
            # Extract the live application error
            app_error = _extract_live_error(content, start_pos)
            if app_error:
                live_errors[test_parameter] = app_error

    # Phase 2: Parse FAILURES section for Python tracebacks
    for match in FAILURES_PATTERN.finditer(content):
        test_parameter = match.group(1)
        failure_pos = match.end()

        # Extract Python traceback
        traceback, file_location = _extract_python_traceback(content, failure_pos)

        # Get the app error from phase 1
        app_error = live_errors.get(test_parameter, NO_APP_ERROR)

        # Extract readable test name from parameter
        test_name = test_parameter.split("-")[-1] if "-" in test_parameter else test_parameter

        # Compute error message summary
        error_message = _compute_error_message(app_error, traceback)

        failed_tests.append(
            FailedTest(
                test_name=test_name,
                test_parameter=test_parameter,
                app_error=app_error,
                python_error=traceback,
                file_location=file_location,
                error_message=error_message,
            )
        )

    # Phase 3: Parse ERRORS section for setup errors
    for match in ERRORS_PATTERN.finditer(content):
        test_parameter = match.group(1)
        error_pos = match.end()

        # Extract Python traceback
        traceback, file_location = _extract_python_traceback(content, error_pos)

        # Extract readable test name from parameter
        test_name = test_parameter.split("-")[-1] if "-" in test_parameter else test_parameter

        # Compute error message summary
        error_message = _compute_error_message(SETUP_ERROR, traceback)

        failed_tests.append(
            FailedTest(
                test_name=test_name,
                test_parameter=test_parameter,
                app_error=SETUP_ERROR,
                python_error=traceback,
                file_location=file_location,
                error_message=error_message,
            )
        )

    return failed_tests


def parse_pytest_log(log_file_path: str) -> dict:
    """Parse pytest log file and extract test results."""

    log_file = _find_log_file(log_file_path)
    if not log_file:
        return {
            "status": STATUS_NO_LOG,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "time": "N/A",
        }

    try:
        with open(log_file, encoding="utf-8") as f:
            content = f.read()

        # Parse the summary line to get counts
        passed = failed = errors = 0
        time = "N/A"

        for line in content.split("\n"):
            if _is_summary_line(line):
                passed, failed, errors, time = _parse_summary_line(line)
                break

        # Determine status based on actual counts, not string search
        # (searching for "FAILED" or "ERROR" gives false positives from log messages)
        if failed > 0 or errors > 0:
            status = STATUS_FAIL
        else:
            status = STATUS_PASS

        return {
            "status": status,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "time": time,
        }

    except Exception as e:
        print(f"  ERROR: Exception parsing log file {log_file}: {e}")
        return {
            "status": STATUS_ERROR,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "time": "N/A",
        }


def find_artifact_directories(artifacts_path: str) -> list[str]:
    """Find all sanity test artifact directories."""
    pattern = os.path.join(artifacts_path, "sanity-test-results-*")
    return glob.glob(pattern)


def process_artifacts(artifacts_path: str) -> tuple[list[TestResult], dict[str, list[FailedTest]]]:
    """Process all artifact directories and extract test results.

    Returns: (results, failed_tests_by_version)
    """
    artifact_dirs = find_artifact_directories(artifacts_path)
    results = []
    failed_tests_by_version = {}

    for artifact_dir in artifact_dirs:
        if not os.path.isdir(artifact_dir):
            continue

        # Extract version name from directory
        version = os.path.basename(artifact_dir).replace("sanity-test-results-", "")
        log_file = os.path.join(artifact_dir, LOG_FILE_CLEAN)

        print(f"Processing {version}...")

        # Parse the log file
        log_data = parse_pytest_log(log_file)

        result = TestResult(
            version=version,
            status=log_data["status"],
            passed=log_data["passed"],
            failed=log_data["failed"],
            errors=log_data["errors"],
            time=log_data["time"],
        )

        results.append(result)

        # Extract failed test details if there are failures
        if log_data["failed"] > 0 or log_data["errors"] > 0:
            failed_tests = extract_failed_test_details(log_file)
            if failed_tests:
                failed_tests_by_version[version] = failed_tests

    # Sort results by version name for consistent display
    results.sort(key=lambda x: x.version)
    return results, failed_tests_by_version


def _analyze_test_failures_across_versions(
    failed_tests_by_version: dict[str, list[FailedTest]],
) -> dict:
    """Analyze which tests failed across different versions.

    Returns dict with:
    - test_failures: dict[test_parameter] -> {versions: list, count: int, sample_error: FailedTest}
    - total_versions: int
    """
    test_failures = {}

    for version, failed_tests in failed_tests_by_version.items():
        for test in failed_tests:
            if test.test_parameter not in test_failures:
                test_failures[test.test_parameter] = {
                    "versions": [],
                    "count": 0,
                    "sample_error": test,  # Keep one example for error details
                    "test_name": test.test_name,
                }
            test_failures[test.test_parameter]["versions"].append(version)
            test_failures[test.test_parameter]["count"] += 1

    total_versions = len(failed_tests_by_version)

    return {"test_failures": test_failures, "total_versions": total_versions}


def _compute_error_message(app_error: str, python_error: str) -> str:
    """Compute a concise error message from app error and Python error.

    Args:
        app_error: Application error string
        python_error: Python traceback string

    Returns:
        Concise error message suitable for display (max 150 chars for app errors)
    """
    # Try app error first (but skip generic messages)
    if app_error and app_error not in (NO_APP_ERROR, SETUP_ERROR):
        for line in app_error.split("\n"):
            # Extract JSON message field or explicit error message
            if '"message":' in line or "Error Message:" in line:
                clean_line = line.strip().replace('"message":', "").replace(",", "").strip()
                if clean_line:
                    return clean_line[:150]

        # Fallback: first ERROR line from app logs
        error_log_lines = [line.strip() for line in app_error.split("\n") if "ERROR" in line]
        if error_log_lines:
            return error_log_lines[0][:150]

    # Try Python error (last line usually has the exception)
    if python_error:
        error_lines = [line.strip() for line in python_error.split("\n") if line.strip()]
        if error_lines:
            return error_lines[-1]

    return "Unknown error"


def _get_passed_versions(all_versions: set[str], failed_versions: list[str]) -> list[str]:
    """Compute which versions passed given the failed versions."""
    return sorted(all_versions - set(failed_versions))


def _group_by_error(test_list: list[tuple]) -> dict[str, list[tuple]]:
    """Group tests by their error message.

    Args:
        test_list: List of (test_param, data) tuples

    Returns:
        Dict mapping error_message -> list of (test_param, data) tuples with that error
    """
    grouped = {}
    for test_param, data in test_list:
        error_msg = data["sample_error"].error_message
        if error_msg not in grouped:
            grouped[error_msg] = []
        grouped[error_msg].append((test_param, data))
    return grouped


def _write_failure_group(
    f,
    error_msg: str,
    tests: list[tuple[str, dict]],
    all_versions: set[str],
    show_versions: bool = False,
    summary_suffix: str = "",
) -> None:
    """Write a group of tests with the same error to output file.

    Args:
        f: File handle to write to
        error_msg: The error message
        tests: List of (test_param, data) tuples with this error
        all_versions: Set of all version names
        show_versions: Whether to show which versions passed/failed
        summary_suffix: Additional text for the summary line (e.g., "on same environment")
    """
    if len(tests) == 1:
        # Single test with this error
        test_param, data = tests[0]
        f.write(f"- **{data['test_name']}** (`{test_param}`)\n")

        if show_versions:
            failed_versions = sorted(data["versions"])
            passed_versions = _get_passed_versions(all_versions, data["versions"])

            if len(failed_versions) == 1:
                f.write(f"  - ❌ **Failed on:** `{failed_versions[0]}`\n")
            else:
                f.write(
                    f"  - ❌ **Failed on ({len(failed_versions)}):** {', '.join(f'`{v}`' for v in failed_versions)}\n"
                )

            if passed_versions:
                f.write(
                    f"  - ✅ **Passed on ({len(passed_versions)}):** {', '.join(f'`{v}`' for v in passed_versions)}\n"
                )

        f.write(f"  - Error: {error_msg}\n\n")
    else:
        # Multiple tests with same error - use collapsible section
        f.write("<details>\n")
        summary_text = f"<summary><b>{len(tests)} tests</b> with same error"
        if summary_suffix:
            summary_text += f" {summary_suffix}"
        summary_text += "</summary>\n\n"
        f.write(summary_text)

        f.write(f"**Error:** {error_msg}\n\n")

        if show_versions:
            # Show which environments failed (use first test as representative)
            failed_versions = sorted(tests[0][1]["versions"])
            passed_versions = _get_passed_versions(all_versions, tests[0][1]["versions"])
            f.write(
                f"- ❌ **Failed on ({len(failed_versions)}):** {', '.join(f'`{v}`' for v in failed_versions)}\n"
            )
            f.write(
                f"- ✅ **Passed on ({len(passed_versions)}):** {', '.join(f'`{v}`' for v in passed_versions)}\n\n"
            )

        f.write("**Affected tests:**\n")
        for test_param, data in sorted(tests, key=lambda x: x[1]["test_name"]):
            f.write(f"- `{data['test_name']}` ({test_param})\n")
        f.write("\n</details>\n\n")


def generate_github_summary(
    results: list[TestResult], failed_tests_by_version: dict[str, list[FailedTest]]
):
    """Generate GitHub step summary with test results."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        print("GITHUB_STEP_SUMMARY environment variable not set")
        return

    # Calculate overall statistics
    total_passed = sum(r.passed for r in results)
    total_failed = sum(r.failed for r in results)
    total_errors = sum(r.errors for r in results)
    overall_status = STATUS_PASS if total_failed == 0 and total_errors == 0 else STATUS_FAIL

    with open(summary_file, "a") as f:
        # Main header
        f.write("# 🧪 Sanity Test Results Summary\n\n")

        # Results table
        f.write("| Version | Status | Passed | Failed | Errors | Total Time |\n")
        f.write("|---------|--------|--------|--------|--------|------------|\n")

        for result in results:
            f.write(
                f"| {result.version} | {result.status} | {result.passed} | "
                f"{result.failed} | {result.errors} | {result.time} |\n"
            )

        # Overall summary
        f.write("\n## 📊 Overall Summary\n")
        f.write(f"- **Overall Status:** {overall_status}\n")
        f.write(f"- **Total Passed:** {total_passed}\n")
        f.write(f"- **Total Failed:** {total_failed}\n")
        f.write(f"- **Total Errors:** {total_errors}\n")
        f.write(f"- **Environments Tested:** {len(results)}\n\n")

        # Failed test details
        if failed_tests_by_version:
            f.write("---\n\n")
            f.write("## ❌ Failed Test Details\n\n")
            f.write("> Click on each test to expand detailed error information\n\n")

            for version in sorted(failed_tests_by_version.keys()):
                failed_tests = failed_tests_by_version[version]
                f.write(f"### 📦 {version}\n")
                f.write(
                    f"**{len(failed_tests)} test{'s' if len(failed_tests) != 1 else ''} failed**\n\n"
                )

                # Check for connector import failures (CRITICAL - app won't load at all)
                import_errors = [
                    t
                    for t in failed_tests
                    if CONNECTOR_IMPORT_FAILED in t.error_message
                    or CONNECTOR_IMPORT_FAILED in t.app_error
                    or CONNECTOR_IMPORT_FAILED in t.python_error
                ]

                if import_errors:
                    f.write("> [!CAUTION]\n")
                    f.write("> **🚨 CRITICAL: Connector Import Failure**\n")
                    f.write(">\n")
                    f.write(
                        "> The connector module failed to import. This is a **critical error** that prevents the app from loading.\n"
                    )
                    f.write(
                        f"> **{len(import_errors)} test{'s' if len(import_errors) != 1 else ''}** failed because the connector could not be initialized.\n"
                    )
                    f.write(">\n")
                    f.write(
                        "> 🔴 **Root Cause:** Missing dependencies, syntax errors, or incompatible Python modules.\n"
                    )
                    f.write(">\n")
                    f.write(
                        "> 💡 **Action Required:** Fix the import issue immediately - no tests can pass until the connector loads successfully.\n\n"
                    )

                # Check for test connectivity failures (special case)
                connectivity_errors = [
                    t
                    for t in failed_tests
                    if CONNECTIVITY_FAILED_1 in t.error_message
                    or CONNECTIVITY_FAILED_2 in t.error_message
                ]

                # Only show connectivity warning if there's no import error (import is more critical)
                if connectivity_errors and not import_errors:
                    f.write("> [!WARNING]\n")
                    f.write("> **⚠️ Test Connectivity Failed**\n")
                    f.write(">\n")
                    f.write(
                        "> Test connectivity failure detected. This typically causes cascading failures across the entire test suite.\n"
                    )
                    f.write(
                        f"> **{len(connectivity_errors)} test{'s' if len(connectivity_errors) != 1 else ''}** failed due to connectivity issues.\n"
                    )
                    f.write(">\n")
                    f.write(
                        "> 💡 **Action Required:** Fix the connectivity issue first, then re-run tests.\n\n"
                    )

                # Group tests by error message
                tests_by_error = {}
                for test in failed_tests:
                    error_key = test.error_message
                    if error_key not in tests_by_error:
                        tests_by_error[error_key] = []
                    tests_by_error[error_key].append(test)

                # Show grouped results
                for error_msg, tests in sorted(tests_by_error.items(), key=lambda x: -len(x[1])):
                    if len(tests) == 1:
                        # Single test with this error - show full details
                        test = tests[0]
                        f.write("<details>\n")
                        f.write(
                            f"<summary><b>{test.test_name}</b> — <code>{test.test_parameter}</code></summary>\n\n"
                        )

                        # File location at top for quick reference
                        if test.file_location:
                            f.write(f"**📍 Location:** `{test.file_location}`\n\n")

                        # Application error
                        if test.app_error and test.app_error != NO_APP_ERROR:
                            f.write("#### 🔴 Application Error\n\n")
                            f.write("```log\n")
                            f.write(f"{test.app_error}\n")
                            f.write("```\n\n")

                        # Python traceback
                        if test.python_error:
                            f.write("#### 🐍 Python Traceback\n\n")
                            f.write("```python\n")
                            f.write(f"{test.python_error}\n")
                            f.write("```\n\n")

                        f.write("</details>\n\n")
                    else:
                        # Multiple tests with same error - show summary + one detailed example
                        f.write("<details>\n")
                        f.write(
                            f"<summary><b>{len(tests)} tests</b> with same error — click for details</summary>\n\n"
                        )

                        f.write(f"**Error:** {error_msg}\n\n")

                        f.write("**Affected tests:**\n")
                        for test in sorted(tests, key=lambda x: x.test_name):
                            f.write(f"- `{test.test_name}` ({test.test_parameter})\n")

                        f.write("\n---\n\n")
                        f.write("**Example detailed error** (from first test):\n\n")

                        example = tests[0]
                        if example.file_location:
                            f.write(f"**📍 Location:** `{example.file_location}`\n\n")

                        if example.app_error and example.app_error != NO_APP_ERROR:
                            f.write("#### 🔴 Application Error\n\n")
                            f.write("```log\n")
                            f.write(f"{example.app_error}\n")
                            f.write("```\n\n")

                        if example.python_error:
                            f.write("#### 🐍 Python Traceback\n\n")
                            f.write("```python\n")
                            f.write(f"{example.python_error}\n")
                            f.write("```\n\n")

                        f.write("</details>\n\n")

                f.write("\n")

        # Cross-environment analysis
        if failed_tests_by_version:
            analysis = _analyze_test_failures_across_versions(failed_tests_by_version)
            test_failures = analysis["test_failures"]
            total_versions = analysis["total_versions"]

            f.write("---\n\n")
            f.write("## 🔍 Cross-Environment Analysis\n\n")
            f.write("> Analyzing test failure patterns across all environments\n\n")

            # Categorize by failure count
            universal = []  # Failed on all versions
            majority = []  # Failed on 4-5 versions
            partial = []  # Failed on 2-3 versions
            isolated = []  # Failed on only 1 version

            for test_param, data in test_failures.items():
                count = data["count"]
                if count == total_versions:
                    universal.append((test_param, data))
                elif count >= 4:
                    majority.append((test_param, data))
                elif count >= 2:
                    partial.append((test_param, data))
                else:
                    isolated.append((test_param, data))

            # Universal failures
            if universal:
                f.write(
                    f"### 🔴 Universal Failures ({len(universal)} test{'s' if len(universal) != 1 else ''})\n"
                )
                f.write(
                    f"*Failed on all {total_versions} environments - likely indicates a fundamental issue*\n\n"
                )

                # Group by error message
                grouped = _group_by_error(universal)
                all_versions = set(failed_tests_by_version.keys())

                for error_msg, tests in sorted(grouped.items(), key=lambda x: -len(x[1])):
                    _write_failure_group(f, error_msg, tests, all_versions, show_versions=False)

            # Isolated failures (most interesting!)
            if isolated:
                f.write(
                    f"### ⚠️ Isolated Failures ({len(isolated)} test{'s' if len(isolated) != 1 else ''})\n"
                )
                f.write(
                    "*Failed on only 1 environment - may indicate environment-specific issues*\n\n"
                )
                all_versions = set(failed_tests_by_version.keys())

                # Group by error message
                grouped = _group_by_error(isolated)

                for error_msg, tests in sorted(grouped.items(), key=lambda x: -len(x[1])):
                    _write_failure_group(
                        f,
                        error_msg,
                        tests,
                        all_versions,
                        show_versions=True,
                        summary_suffix="on same environment",
                    )

            # Partial failures
            if partial:
                f.write(
                    f"### 🟡 Partial Failures ({len(partial)} test{'s' if len(partial) != 1 else ''})\n"
                )
                f.write("*Failed on some environments - inconsistent behavior*\n\n")
                all_versions = set(failed_tests_by_version.keys())

                # Group by error message
                grouped = _group_by_error(partial)

                for error_msg, tests in sorted(grouped.items(), key=lambda x: -len(x[1])):
                    _write_failure_group(
                        f,
                        error_msg,
                        tests,
                        all_versions,
                        show_versions=True,
                        summary_suffix="pattern",
                    )

            # Majority failures
            if majority:
                f.write(
                    f"### 🟠 Majority Failures ({len(majority)} test{'s' if len(majority) != 1 else ''})\n"
                )
                f.write(
                    f"*Failed on most environments ({len(majority)} tests failed on 4-5 environments)*\n\n"
                )
                all_versions = set(failed_tests_by_version.keys())

                # Group by error message
                grouped = _group_by_error(majority)

                for error_msg, tests in sorted(grouped.items(), key=lambda x: -len(x[1])):
                    _write_failure_group(
                        f,
                        error_msg,
                        tests,
                        all_versions,
                        show_versions=True,
                        summary_suffix="on most environments",
                    )


def main():
    parser = argparse.ArgumentParser(description="Aggregate sanity test results")
    parser.add_argument(
        "--artifacts-path", required=True, help="Path to downloaded artifacts directory"
    )

    args = parser.parse_args()

    if not os.path.exists(args.artifacts_path):
        print(f"Error: Artifacts path {args.artifacts_path} does not exist")
        exit(1)

    print("Starting sanity test result aggregation...")

    # Process all artifacts
    results, failed_tests_by_version = process_artifacts(args.artifacts_path)

    if not results:
        print("Warning: No sanity test artifacts found")
        exit(0)

    print(f"Found {len(results)} test result sets")

    # Generate GitHub summary
    generate_github_summary(results, failed_tests_by_version)

    # Print summary to console as well
    print("\n=== SANITY TEST SUMMARY ===")
    for result in results:
        print(
            f"{result.version}: {result.status} ({result.passed}P/{result.failed}F/{result.errors}E)"
        )

    total_failed = sum(r.failed for r in results)
    total_errors = sum(r.errors for r in results)

    if total_failed > 0 or total_errors > 0:
        print(f"\nOverall: FAILED ({total_failed} failed, {total_errors} errors)")
        exit(1)
    else:
        print("\nOverall: PASSED")
        print("Sanity test aggregation completed successfully!")


if __name__ == "__main__":
    main()
