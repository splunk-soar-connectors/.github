#!/usr/bin/env python3
"""
Aggregate sanity test results from multiple matrix job artifacts.
Creates a comprehensive GitHub step summary with test results.
"""

import argparse
import os
import re
import glob
from typing import NamedTuple

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


def _find_log_file(log_file_path: str) -> str | None:
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
    return (
        stripped.startswith("==")
        and stripped.endswith("===")
        and ("failed" in line or "passed" in line)
    )


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


def process_artifacts(artifacts_path: str) -> list[TestResult]:
    """Process all artifact directories and extract test results."""
    artifact_dirs = find_artifact_directories(artifacts_path)
    results = []

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

    # Sort results by version name for consistent display
    results.sort(key=lambda x: x.version)
    return results


def generate_github_summary(results: list[TestResult]):
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
    results = process_artifacts(args.artifacts_path)

    if not results:
        print("Warning: No sanity test artifacts found")
        exit(0)

    print(f"Found {len(results)} test result sets")

    # Generate GitHub summary
    generate_github_summary(results)

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
