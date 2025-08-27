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
    print(f"  DEBUG: Checking log file: {log_file_path}")

    if not os.path.exists(log_file_path):
        print(f"  DEBUG: Log file does not exist: {log_file_path}")
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

        print(f"  DEBUG: Log file size: {len(content)} characters")

        # Check if there are failures or errors
        has_failures = "FAILED" in content or "ERROR" in content
        status = "❌ FAIL" if has_failures else "✅ PASS"
        print(f"  DEBUG: Has failures: {has_failures}, Status: {status}")

        # Find the summary line (e.g., "2 failed, 23 passed, 4 deselected, 2 errors, 4 rerun in 428.31s")
        summary_pattern = r"=+ (.+) in ([\d:.]+)s? =+$"
        summary_matches = re.findall(summary_pattern, content, re.MULTILINE)
        print(f"  DEBUG: Found {len(summary_matches)} summary lines")

        if summary_matches:
            print(f"  DEBUG: Last summary line: {summary_matches[-1]}")

        passed = failed = errors = 0
        time = "N/A"

        if summary_matches:
            summary_text, time_str = summary_matches[-1]  # Get the last (final) summary
            time = f"{time_str}s"
            print(f"  DEBUG: Summary text: '{summary_text}', Time: {time}")

            # Extract numbers from summary text
            passed_match = re.search(r"(\d+) passed", summary_text)
            failed_match = re.search(r"(\d+) failed", summary_text)
            error_match = re.search(r"(\d+) error", summary_text)

            passed = int(passed_match.group(1)) if passed_match else 0
            failed = int(failed_match.group(1)) if failed_match else 0
            errors = int(error_match.group(1)) if error_match else 0

            print(f"  DEBUG: Parsed - Passed: {passed}, Failed: {failed}, Errors: {errors}")
        else:
            print("  DEBUG: No summary matches found, trying alternative patterns")
            # Try alternative patterns for summary lines
            alt_patterns = [
                r"== (\d+) failed, (\d+) passed.*in ([\d:.]+)s ==",
                r"== (\d+) passed.*in ([\d:.]+)s ==",
                r"(\d+) failed, (\d+) passed",
            ]

            for pattern in alt_patterns:
                matches = re.search(pattern, content)
                if matches:
                    print(f"  DEBUG: Found alternative pattern: {matches.groups()}")
                    break

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
            print(f"  DEBUG: Found {len(failure_details)} failure details")

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
        print(f"  ERROR: Exception parsing log file {log_file_path}: {e}")
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

        # Debug: Show directory contents
        if os.path.isdir(artifact_dir):
            files_in_dir = os.listdir(artifact_dir)
            print(f"  DEBUG: Files in {artifact_dir}: {files_in_dir}")
        else:
            print(f"  DEBUG: Directory does not exist: {artifact_dir}")

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


def extract_individual_test_results(results: list[TestResult]) -> dict[str, dict[str, str]]:
    """Extract individual test results for comparison across versions.

    Returns: dict[test_name, dict[version, status]]
    """
    test_results = {}

    for result in results:
        if not result.log_exists:
            continue

        version = result.version

        # Try to find the log file and extract individual test results
        artifact_dir = f"downloaded-artifacts/sanity-test-results-{version}"
        log_file = os.path.join(artifact_dir, "pytest-output.log")

        if not os.path.exists(log_file):
            continue

        try:
            with open(log_file, encoding="utf-8") as f:
                content = f.read()

            lines = content.split("\n")
            for line in lines:
                # Look for individual test results (PASSED, FAILED, ERROR)
                if "PASSED" in line or "FAILED" in line or "ERROR" in line:
                    # Extract test name - pattern like: suite/apps/maxmind/test::TestClass::test_method PASSED
                    match = re.match(r"^([^:]+::[^:]+::[^:\s]+)\s+(PASSED|FAILED|ERROR)", line)
                    if match:
                        test_name = match.group(1).strip()
                        test_status = match.group(2).strip()

                        if test_name not in test_results:
                            test_results[test_name] = {}
                        test_results[test_name][version] = test_status

        except Exception as e:
            print(f"  DEBUG: Error extracting individual tests from {log_file}: {e}")

    return test_results


def find_inconsistent_tests(
    test_results: dict[str, dict[str, str]], all_versions: list[str]
) -> list[dict]:
    """Find tests that have different outcomes across versions."""
    inconsistent = []

    for test_name, version_results in test_results.items():
        # Skip if test doesn't have results for multiple versions
        if len(version_results) < 2:
            continue

        # Check if all results are the same
        statuses = set(version_results.values())
        if len(statuses) > 1:
            # This test has different results across versions
            passed_versions = [v for v, s in version_results.items() if s == "PASSED"]
            failed_versions = [v for v, s in version_results.items() if s in ["FAILED", "ERROR"]]

            inconsistent.append(
                {
                    "test_name": test_name,
                    "passed_versions": passed_versions,
                    "failed_versions": failed_versions,
                    "all_results": version_results,
                }
            )

    return inconsistent


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

        # Add test comparison analysis
        print("Analyzing test consistency across versions...")
        all_versions = [r.version for r in results]
        test_results = extract_individual_test_results(results)
        inconsistent_tests = find_inconsistent_tests(test_results, all_versions)

        if inconsistent_tests:
            f.write("## ⚠️ Version-Specific Test Issues\n")
            f.write(
                f"Found {len(inconsistent_tests)} tests with different results across versions:\n\n"
            )

            for issue in inconsistent_tests[:10]:  # Limit to first 10 for readability
                test_short_name = issue["test_name"].split("::")[
                    -1
                ]  # Get just the test method name

                f.write(f"### 🔍 `{test_short_name}`\n")
                if issue["failed_versions"]:
                    f.write(f"- **❌ Failed on:** {', '.join(sorted(issue['failed_versions']))}\n")
                if issue["passed_versions"]:
                    f.write(f"- **✅ Passed on:** {', '.join(sorted(issue['passed_versions']))}\n")
                f.write("\n")

            if len(inconsistent_tests) > 10:
                f.write(f"... and {len(inconsistent_tests) - 10} more inconsistent tests\n\n")
        else:
            f.write("## ✅ Test Consistency\n")
            f.write("All tests show consistent results across versions.\n\n")

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

    # Print version-specific test analysis
    print("\n=== VERSION COMPARISON ANALYSIS ===")
    all_versions = [r.version for r in results]
    test_results = extract_individual_test_results(results)
    inconsistent_tests = find_inconsistent_tests(test_results, all_versions)

    if inconsistent_tests:
        print(f"Found {len(inconsistent_tests)} tests with different results across versions:")
        for issue in inconsistent_tests[:5]:  # Show first 5 in console
            test_short_name = issue["test_name"].split("::")[-1]
            failed_vers = ", ".join(sorted(issue["failed_versions"]))
            passed_vers = ", ".join(sorted(issue["passed_versions"]))
            print(f"  • {test_short_name}: FAILED on [{failed_vers}], PASSED on [{passed_vers}]")

        if len(inconsistent_tests) > 5:
            print(
                f"  ... and {len(inconsistent_tests) - 5} more inconsistent tests (see GitHub summary)"
            )
    else:
        print("All tests show consistent results across versions.")

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
