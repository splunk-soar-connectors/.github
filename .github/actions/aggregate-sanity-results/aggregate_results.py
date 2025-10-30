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
    """Check if a line is a pytest summary line."""
    stripped = line.strip()
    # Match lines like: == 2 failed, 23 passed in 227.61s ==
    # Note: Can end with == or ===
    return (
        stripped.startswith("==")
        and ("failed" in line or "passed" in line)
        and ("in " in line and "s" in line)  # Has timing info
    )


def _extract_test_parameter(line: str) -> Optional[str]:
    """Extract test parameter from test name line.

    Example: test_action[pytest/snowflake-disable_user_1_000] -> pytest/snowflake-disable_user_1_000
    """
    match = re.search(r"\[([^\]]+)\]", line)
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
    # Extract everything from "live log call" until the next test or section boundary
    next_boundary = content.find("::test_action[", start_pos)
    if next_boundary == -1:
        next_boundary = content.find("FAILED", start_pos)
    if next_boundary == -1:
        next_boundary = content.find("PASSED", start_pos)
    if next_boundary == -1:
        next_boundary = start_pos + 5000  # Fallback

    error_section = content[start_pos:next_boundary]

    # Stop at certain markers that indicate we've gone too far
    stop_markers = ["RERUN", "------- Captured", "========"]
    for marker in stop_markers:
        marker_pos = error_section.find(marker)
        if marker_pos != -1:
            error_section = error_section[:marker_pos]

    # Clean up and return - strip excessive whitespace but preserve structure
    lines = error_section.strip().split("\n")
    cleaned_lines = [line.rstrip() for line in lines if line.strip()]

    return "\n".join(cleaned_lines)


def _extract_python_traceback(content: str, failure_pos: int) -> tuple[str, str]:
    """Extract Python traceback and file location from FAILURES section.

    Returns: (complete_traceback, file_location)
    """
    # Find the section until the next test or captured log
    lines = content[failure_pos:].split("\n")
    traceback_lines = []
    file_location = ""
    in_traceback = True

    for i, line in enumerate(lines):
        # Stop at captured log section (we get that from live errors)
        if "Captured log call" in line:
            break

        # Stop at next test failure
        if i > 0 and line.startswith("_______") and "TestApp" in line:
            break

        # Stop at short test summary
        if "short test summary" in line.lower():
            break

        # Extract file location from first traceback line
        if 'File "' in line and ", line " in line and not file_location:
            match = re.search(r'File "([^"]+)", line (\d+)', line)
            if match:
                file_path = match.group(1)
                line_num = match.group(2)
                # Shorten path for readability
                if "/actions-runner/_work/" in file_path:
                    file_path = file_path.split("/actions-runner/_work/")[1]
                file_location = f"{file_path}:{line_num}"

        # Collect all traceback lines
        if in_traceback and line.strip():
            traceback_lines.append(line.rstrip())

    # Return complete traceback (don't limit it)
    traceback = "\n".join(traceback_lines)

    return traceback, file_location


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
    execution_pattern = r"(suite/[^\n]+::test_action\[([^\]]+)\])\s*\n-+ live log call -+"
    live_errors = {}

    for match in re.finditer(execution_pattern, content):
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
    failures_pattern = r"_{5,}\s+TestApp\.test_action\[([^\]]+)\]\s+_{5,}"

    for match in re.finditer(failures_pattern, content):
        test_parameter = match.group(1)
        failure_pos = match.end()

        # Extract Python traceback
        traceback, file_location = _extract_python_traceback(content, failure_pos)

        # Get the app error from phase 1
        app_error = live_errors.get(test_parameter, "No application error captured")

        # Extract readable test name from parameter
        # pytest/snowflake-disable_user_1_000 -> disable_user (disable user)
        test_name = test_parameter.split("-")[-1] if "-" in test_parameter else test_parameter

        # Compute error message summary
        error_message = _compute_error_message(app_error, traceback)

        failed_test = FailedTest(
            test_name=test_name,
            test_parameter=test_parameter,
            app_error=app_error,
            python_error=traceback,
            file_location=file_location,
            error_message=error_message,
        )

        failed_tests.append(failed_test)

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

        # Check for failures or errors
        has_failures = "FAILED" in content or "ERROR" in content
        status = STATUS_FAIL if has_failures else STATUS_PASS

        # Parse the summary line
        passed = failed = errors = 0
        time = "N/A"

        for line in content.split("\n"):
            if _is_summary_line(line):
                passed, failed, errors, time = _parse_summary_line(line)
                break

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
        app_error: Application error string (may be "No application error captured")
        python_error: Python traceback string

    Returns:
        Concise error message suitable for display
    """
    # Try app error first
    if app_error and app_error != "No application error captured":
        error_lines = app_error.split("\n")
        for line in error_lines:
            if '"message":' in line or "Error Message:" in line:
                # Extract the message value
                clean_line = line.strip().replace('"message":', "").replace(",", "").strip()
                if clean_line:
                    return clean_line[:150]
        # Fallback: first ERROR line
        error_log_lines = [line.strip() for line in error_lines if "ERROR" in line]
        if error_log_lines:
            return error_log_lines[0][:150]

    # Try Python error
    if python_error:
        error_lines = [line.strip() for line in python_error.split("\n") if line.strip()]
        if error_lines:
            return error_lines[-1]

    return "Unknown error"


def _get_passed_versions(all_versions: set[str], failed_versions: list[str]) -> list[str]:
    """Compute which versions passed given the failed versions."""
    return sorted(all_versions - set(failed_versions))


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

                for i, test in enumerate(failed_tests, 1):
                    f.write("<details>\n")
                    f.write(
                        f"<summary><b>#{i} {test.test_name}</b> — <code>{test.test_parameter}</code></summary>\n\n"
                    )

                    # File location at top for quick reference
                    if test.file_location:
                        f.write(f"**📍 Location:** `{test.file_location}`\n\n")

                    # Application error
                    if test.app_error and test.app_error != "No application error captured":
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
                for test_param, data in sorted(universal, key=lambda x: x[1]["test_name"]):
                    sample = data["sample_error"]
                    f.write(f"- **{data['test_name']}** (`{test_param}`)\n")
                    f.write(f"  - Error: {sample.error_message}\n")
                    f.write("\n")

            # Isolated failures (most interesting!)
            if isolated:
                f.write(
                    f"### ⚠️ Isolated Failures ({len(isolated)} test{'s' if len(isolated) != 1 else ''})\n"
                )
                f.write(
                    "*Failed on only 1 environment - may indicate environment-specific issues*\n\n"
                )
                all_versions = set(failed_tests_by_version.keys())
                for test_param, data in sorted(isolated, key=lambda x: x[1]["test_name"]):
                    sample = data["sample_error"]
                    failed_on = data["versions"][0]
                    passed_on = _get_passed_versions(all_versions, data["versions"])

                    f.write(f"- **{data['test_name']}** (`{test_param}`)\n")
                    f.write(f"  - ❌ **Failed on:** `{failed_on}`\n")
                    f.write(f"  - ✅ **Passed on:** {', '.join(f'`{v}`' for v in passed_on)}\n")
                    f.write(f"  - Issue: {sample.error_message}\n")
                    f.write("\n")

            # Partial failures
            if partial:
                f.write(
                    f"### 🟡 Partial Failures ({len(partial)} test{'s' if len(partial) != 1 else ''})\n"
                )
                f.write("*Failed on some environments - inconsistent behavior*\n\n")
                all_versions = set(failed_tests_by_version.keys())
                for test_param, data in sorted(partial, key=lambda x: x[1]["test_name"]):
                    sample = data["sample_error"]
                    failed_versions = sorted(data["versions"])
                    passed_versions = _get_passed_versions(all_versions, data["versions"])

                    f.write(f"- **{data['test_name']}** (`{test_param}`)\n")
                    f.write(
                        f"  - ❌ **Failed on ({data['count']}):** {', '.join(f'`{v}`' for v in failed_versions)}\n"
                    )
                    f.write(
                        f"  - ✅ **Passed on ({len(passed_versions)}):** {', '.join(f'`{v}`' for v in passed_versions)}\n"
                    )
                    f.write(f"  - Issue: {sample.error_message}\n")
                    f.write("\n")

            # Majority failures
            if majority:
                f.write(
                    f"### 🟠 Majority Failures ({len(majority)} test{'s' if len(majority) != 1 else ''})\n"
                )
                f.write(
                    f"*Failed on most environments ({len(majority)} tests failed on 4-5 environments)*\n\n"
                )
                all_versions = set(failed_tests_by_version.keys())
                for test_param, data in sorted(majority, key=lambda x: x[1]["test_name"]):
                    passed_versions = _get_passed_versions(all_versions, data["versions"])

                    f.write(
                        f"- **{data['test_name']}** (`{test_param}`) - Failed on {data['count']}/{total_versions}\n"
                    )
                    if passed_versions:
                        f.write(
                            f"  - ✅ Only passed on: {', '.join(f'`{v}`' for v in passed_versions)}\n"
                        )
                    f.write("\n")


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
