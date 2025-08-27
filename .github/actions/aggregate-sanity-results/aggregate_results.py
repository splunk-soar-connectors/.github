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


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI color codes from text."""
    # Pattern to match ANSI escape sequences including ESC character (\x1b or \033)
    # This handles: \x1b[31m, \033[1m, \x1b[0m, etc.
    ansi_pattern = r"\x1b\[[0-9;]*[a-zA-Z]"
    # Also handle bracket-only patterns like [31m that might remain
    bracket_pattern = r"\[[\d;]*m"

    # Apply both patterns
    text = re.sub(ansi_pattern, "", text)
    text = re.sub(bracket_pattern, "", text)

    return text


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
            raw_content = f.read()

        # Strip ANSI color codes for easier parsing
        content = strip_ansi_codes(raw_content)

        print(
            f"  DEBUG: Log file size: {len(raw_content)} characters (raw), {len(content)} characters (clean)"
        )

        # Check if there are failures or errors
        has_failures = "FAILED" in content or "ERROR" in content
        status = "❌ FAIL" if has_failures else "✅ PASS"
        print(f"  DEBUG: Has failures: {has_failures}, Status: {status}")

        # Count individual test results instead of relying on summary
        # Look for lines like: "suite/apps/test.py::TestClass::test_method PASSED [  3%]"
        lines = content.split("\n")

        passed = failed = errors = 0
        time = "N/A"

        # Parse pytest output format:
        # 1. Test name on its own line: "suite/apps/test.py::TestClass::test_name[param]"
        # 2. Various log output follows
        # 3. Result on separate line with ANSI: "[color]RESULT[0m[color]...[ nn%][0m"

        individual_results = []
        current_test = None

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # Check if this line is a test name (starts with suite/apps/ and ends with ])
            if line.startswith("suite/apps/") and line.endswith("]"):
                # This is a new test starting
                current_test = line
                continue

            # Check if this line contains a test result
            # After ANSI stripping, results appear as just: "PASSED", "FAILED", "ERROR"
            result_match = re.match(r"^(PASSED|FAILED|ERROR)\s+\[\s*\d+%\]", line)
            if result_match:
                result = result_match.group(1)

                if current_test:
                    individual_results.append((current_test, result))

                    if result == "PASSED":
                        passed += 1
                    elif result == "FAILED":
                        failed += 1
                    elif result == "ERROR":
                        errors += 1

                    # Reset for next test
                    current_test = None
                else:
                    # Found result without test name, just count it
                    if result == "PASSED":
                        passed += 1
                    elif result == "FAILED":
                        failed += 1
                    elif result == "ERROR":
                        errors += 1

        print("  DEBUG: Individual test parsing found:")
        print(f"    - PASSED: {passed}")
        print(f"    - FAILED: {failed}")
        print(f"    - ERROR: {errors}")
        print(f"    - Total individual results parsed: {len(individual_results)}")

        # Try to extract overall execution time from summary line if available
        for line in lines:
            if "==" in line and "in " in line and "s" in line:
                time_match = re.search(r"in ([\d:.]+)s", line)
                if time_match:
                    time = f"{time_match.group(1)}s"
                    print(f"  DEBUG: Extracted time from summary: {time}")
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
                raw_content = f.read()

            # Strip ANSI color codes for easier parsing
            content = strip_ansi_codes(raw_content)
            lines = content.split("\n")

            # Use the same logic as in parse_pytest_log for consistency
            # Parse pytest output format:
            # 1. Test name on its own line: "suite/apps/test.py::TestClass::test_name[param]"
            # 2. Various log output follows
            # 3. Result on separate line with ANSI: "[color]RESULT[0m[color]...[ nn%][0m"

            version_results = []
            current_test = None

            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                # Check if this line is a test name (starts with suite/apps/ and ends with ])
                if line.startswith("suite/apps/") and line.endswith("]"):
                    # This is a new test starting
                    current_test = line
                    continue

                # Check if this line contains a test result
                # After ANSI stripping, results appear as just: "PASSED", "FAILED", "ERROR"
                result_match = re.match(r"^(PASSED|FAILED|ERROR)\s+\[\s*\d+%\]", line)
                if result_match:
                    test_status = result_match.group(1)

                    if current_test:
                        version_results.append((current_test, test_status))

                        if current_test not in test_results:
                            test_results[current_test] = {}
                        test_results[current_test][version] = test_status

                        # Reset for next test
                        current_test = None

            print(f"    Found {len(version_results)} individual test results for {version}")

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
