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


class TestResult(NamedTuple):
    """Test result data for a single matrix job."""

    version: str
    status: str
    passed: int
    failed: int
    errors: int
    time: str
    log_exists: bool
    failure_details: list[str]


def parse_pytest_log(log_file_path: str) -> dict:
    """Parse pytest log file and extract test results."""
    if not os.path.exists(log_file_path):
        return {
            "status": "⚠️ NO LOG",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "time": "N/A",
            "log_exists": False,
            "failure_details": [],
        }

    try:
        with open(log_file_path, encoding="utf-8") as f:
            content = f.read()

        # Check if there are failures or errors
        has_failures = "FAILED" in content or "ERROR" in content
        status = "❌ FAIL" if has_failures else "✅ PASS"

        # Find the summary line (e.g., "2 failed, 23 passed, 4 deselected, 2 errors, 4 rerun in 428.31s")
        summary_pattern = r"=+ (.+) in ([\d:.]+)s? =+$"
        summary_matches = re.findall(summary_pattern, content, re.MULTILINE)

        passed = failed = errors = 0
        time = "N/A"

        if summary_matches:
            summary_text, time_str = summary_matches[-1]  # Get the last (final) summary
            time = f"{time_str}s"

            # Extract numbers from summary text
            passed_match = re.search(r"(\d+) passed", summary_text)
            failed_match = re.search(r"(\d+) failed", summary_text)
            error_match = re.search(r"(\d+) error", summary_text)

            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            errors = int(error_match.group(1)) if error_match else 0

        # Extract failure details if there are failures
        failure_details = []
        if has_failures:
            # Find lines with FAILED or ERROR and some context
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "FAILED" in line or "ERROR" in line:
                    # Get some context around the failure
                    start = max(0, i - 1)
                    end = min(len(lines), i + 5)
                    context = "\n".join(lines[start:end])
                    failure_details.append(context)
                    if len(failure_details) >= 3:  # Limit to first 3 failures
                        break

        return {
            "status": status,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "time": time,
            "log_exists": True,
            "failure_details": failure_details,
        }

    except Exception as e:
        print(f"Error parsing log file {log_file_path}: {e}")
        return {
            "status": "⚠️ ERROR",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "time": "N/A",
            "log_exists": False,
            "failure_details": [f"Failed to parse log: {e!s}"],
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
        log_file = os.path.join(artifact_dir, "pytest-output.log")

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
            log_exists=log_data["log_exists"],
            failure_details=log_data["failure_details"],
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
    overall_status = "✅ PASS" if total_failed == 0 and total_errors == 0 else "❌ FAIL"

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

        # Failure details if any
        failed_results = [r for r in results if r.failed > 0 or r.errors > 0]
        if failed_results:
            f.write("## 🔍 Failed Test Details\n\n")

            for result in failed_results:
                if result.failure_details:
                    f.write(f"### {result.version} Failures:\n")
                    f.write("```\n")
                    # Show first failure detail, truncated for readability
                    detail = result.failure_details[0]
                    lines = detail.split("\n")[:20]  # Limit to 20 lines
                    f.write("\n".join(lines))
                    if len(result.failure_details) > 1:
                        f.write(f"\n... and {len(result.failure_details) - 1} more failures")
                    f.write("\n```\n\n")

        # Add links to artifacts for detailed investigation
        f.write("## 📁 Artifact Downloads\n")
        f.write("Download individual test artifacts for detailed investigation:\n")
        for result in results:
            status_icon = "✅" if result.status == "✅ PASS" else "❌"
            f.write(f"- {status_icon} `sanity-test-results-{result.version}.zip`\n")


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
