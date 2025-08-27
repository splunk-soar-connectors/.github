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
    # Comprehensive pattern to match various ANSI escape sequences
    ansi_patterns = [
        r"\x1b\[[0-9;]*[a-zA-Z]",  # Standard ANSI codes like \x1b[31m, \x1b[0m
        r"\033\[[0-9;]*[a-zA-Z]",  # Octal variant \033[31m
        r"\[[\d;]*m",  # Bracket codes that might remain [31m, [0m
        r"\[[\d;]*[a-zA-Z]",  # Other bracket variants [31A, [2K
    ]

    clean_text = text
    for pattern in ansi_patterns:
        clean_text = re.sub(pattern, "", clean_text)

    return clean_text


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

    # Check for multiple possible log file names
    possible_log_files = [
        log_file_path,  # pytest-output.log (preferred)
        log_file_path.replace(
            "pytest-output.log", "pytest-output-raw.log"
        ),  # pytest-output-raw.log (fallback)
    ]

    actual_log_file = None
    for possible_file in possible_log_files:
        if os.path.exists(possible_file):
            actual_log_file = possible_file
            print(f"  DEBUG: Found log file: {actual_log_file}")
            break

    if not actual_log_file:
        print(f"  DEBUG: No log file found at: {possible_log_files}")
        return {
            "status": "⚠️ NO LOG",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "time": "N/A",
            "log_exists": False,
            "failure_details": [],
        }

    # Use the actual log file found
    log_file_path = actual_log_file

    try:
        with open(log_file_path, encoding="utf-8") as f:
            content = f.read()

        # Fallback: Strip ANSI codes if they're still present (in case sed failed)
        if "\x1b[" in content:
            print("  DEBUG: ANSI codes detected, stripping as fallback")
            content = strip_ansi_codes(content)

        print(f"  DEBUG: Log file size: {len(content)} characters")

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
        debug_lines_checked = 0
        debug_test_names_found = 0
        debug_results_found = 0

        for i, line in enumerate(lines):
            debug_lines_checked += 1
            line = line.strip()
            if not line:
                continue

            # Check if this line is a test name (starts with suite/apps/)
            # Handle both patterns:
            # 1. Test name only: "suite/apps/maxmind/maxmind_sanity_test.py::TestApp::test_connectivity_test[maxmind]"
            # 2. Test name + result: "suite/apps/.../test_action[maxmind-geolocate_ip] [32mPASSED[0m[31m [ 18%][0m"
            if line.startswith("suite/apps/") and "::" in line:
                debug_test_names_found += 1

                # Check if this line also contains a result (pattern 2)
                same_line_result = None
                if "PASSED" in line or "FAILED" in line or "ERROR" in line:
                    # Extract the test name part (before any ANSI codes or result indicators)
                    test_name_match = re.match(r"^(suite/apps/[^\[]*\[[^\]]+\])", line)
                    if test_name_match:
                        current_test = test_name_match.group(1)

                        # Extract result from the same line
                        if "PASSED" in line:
                            same_line_result = "PASSED"
                        elif "FAILED" in line:
                            same_line_result = "FAILED"
                        elif "ERROR" in line:
                            same_line_result = "ERROR"
                else:
                    # Pattern 1: Test name only, result will be on next lines
                    if line.endswith("]"):
                        current_test = line
                    else:
                        # Handle cases where test name might be cut off
                        bracket_pos = line.rfind("]")
                        if bracket_pos > 0:
                            current_test = line[: bracket_pos + 1]
                        else:
                            current_test = line

                if debug_test_names_found <= 8:  # Show first 8 test names found
                    print(f"  DEBUG: Found test {debug_test_names_found}: {current_test}")
                    if same_line_result:
                        print(f"    Same-line result: {same_line_result}")

                # Process same-line result immediately
                if same_line_result and current_test:
                    individual_results.append((current_test, same_line_result))
                    debug_results_found += 1

                    if same_line_result == "PASSED":
                        passed += 1
                    elif same_line_result == "FAILED":
                        failed += 1
                    elif same_line_result == "ERROR":
                        errors += 1

                    if debug_results_found <= 12:
                        print(
                            f"    → Same-line result {debug_results_found}: {same_line_result} for {current_test.split('::')[-1]}"
                        )

                    # Reset since we processed this test
                    current_test = None

                continue

            # Check for separate-line results (original pattern)
            # Look for patterns like: "[32mPASSED[0m[32m                    [  3%][0m"
            result_patterns = [
                r"(PASSED|FAILED|ERROR)\s+\[\s*\d+%\]",  # Original pattern
                r"\[[\d;]*m(PASSED|FAILED|ERROR)\[[\d;]*m.*\[\s*\d+%\]",  # With ANSI codes
            ]

            result_found = None
            for pattern in result_patterns:
                result_match = re.search(pattern, line)
                if result_match:
                    result_found = result_match.group(1)
                    break

            if result_found:
                debug_results_found += 1

                if debug_results_found <= 12:  # Show first 12 results found
                    print(
                        f"  DEBUG: Found result {debug_results_found}: '{result_found}' on line {i + 1}"
                    )
                    print(
                        f"    Line: '{line[:80]}...' " + ("(truncated)" if len(line) > 80 else "")
                    )
                    print(
                        f"    Current test: {current_test.split('::')[-1] if current_test and '::' in current_test else current_test}"
                    )

                if current_test:
                    individual_results.append((current_test, result_found))

                    if result_found == "PASSED":
                        passed += 1
                    elif result_found == "FAILED":
                        failed += 1
                    elif result_found == "ERROR":
                        errors += 1

                    print(
                        f"    → Associated with test: {current_test.split('::')[-1] if '::' in current_test else current_test}"
                    )
                    # Reset for next test
                    current_test = None
                else:
                    print("    → No current test to associate with!")
                    # Found result without test name, just count it
                    if result_found == "PASSED":
                        passed += 1
                    elif result_found == "FAILED":
                        failed += 1
                    elif result_found == "ERROR":
                        errors += 1

        print("  DEBUG: Individual test parsing found:")
        print(f"    - PASSED: {passed}")
        print(f"    - FAILED: {failed}")
        print(f"    - ERROR: {errors}")
        print(f"    - Total individual results parsed: {len(individual_results)}")

        # Store individual parsing results for comparison
        individual_passed, individual_failed, individual_errors = passed, failed, errors

        # Parse the summary line for authoritative totals and time
        # Format: "== 2 failed, 23 passed, 4 deselected, 2 errors, 4 rerun in 412.28s (0:06:52) ==="
        summary_passed = summary_failed = summary_errors = 0

        for line in lines:
            # Look for the specific pytest summary line format
            # == 2 failed, 23 passed, 4 deselected, 2 errors, 4 rerun in 414.38s (0:06:52) ===
            if (
                line.strip().startswith("==")
                and line.strip().endswith("===")
                and ("failed" in line or "passed" in line)
            ):
                print(f"  DEBUG: Found summary line: '{line.strip()}'")

                # Extract counts from summary
                passed_match = re.search(r"(\d+) passed", line)
                failed_match = re.search(r"(\d+) failed", line)
                error_match = re.search(r"(\d+) error", line)

                if passed_match:
                    summary_passed = int(passed_match.group(1))
                if failed_match:
                    summary_failed = int(failed_match.group(1))
                if error_match:
                    summary_errors = int(error_match.group(1))

                # Extract time
                time_match = re.search(r"in ([\d:.]+)s", line)
                if time_match:
                    time = f"{time_match.group(1)}s"

                print(
                    f"  DEBUG: Summary counts - Passed: {summary_passed}, Failed: {summary_failed}, Errors: {summary_errors}"
                )
                break

        # Use summary counts as authoritative (individual parsing may miss some tests)
        if summary_passed > 0 or summary_failed > 0 or summary_errors > 0:
            print("  DEBUG: Using summary counts as authoritative")

            # Validate summary counts are reasonable
            total_summary = summary_passed + summary_failed + summary_errors
            if total_summary > 1000 or total_summary < 0:
                print(f"  WARNING: Summary counts seem unrealistic: {total_summary} total tests")

            # Show comparison between individual parsing and summary
            if (
                individual_passed != summary_passed
                or individual_failed != summary_failed
                or individual_errors != summary_errors
            ):
                print("  DEBUG: Discrepancy detected!")
                print(
                    f"    Individual: {individual_passed}P/{individual_failed}F/{individual_errors}E"
                )
                print(f"    Summary:    {summary_passed}P/{summary_failed}F/{summary_errors}E")
                if summary_passed > individual_passed:
                    print(
                        f"    Missing PASSED tests in individual parsing: {summary_passed - individual_passed}"
                    )

            passed, failed, errors = summary_passed, summary_failed, summary_errors
        else:
            print("  DEBUG: No summary found, using individual parsing counts")

            # Validate individual counts as well
            total_individual = individual_passed + individual_failed + individual_errors
            if total_individual == 0:
                print(
                    "  WARNING: No tests found in either summary or individual parsing - possible parsing error"
                )

        # Extract failure details from "short test summary info" section
        failure_details = []
        if has_failures:
            in_summary_section = False
            for line in lines:
                line_stripped = line.strip()

                # Check if we're entering the short test summary section
                if "short test summary info" in line_stripped:
                    in_summary_section = True
                    continue

                # Check if we're leaving the summary section (next == line)
                if in_summary_section and line_stripped.startswith("=="):
                    break

                # Extract FAILED/ERROR test names from summary section
                if in_summary_section and (
                    line_stripped.startswith("FAILED ") or line_stripped.startswith("ERROR ")
                ):
                    # Extract just the test name part
                    parts = line_stripped.split(" ", 1)
                    if len(parts) > 1:
                        test_name = parts[1].strip()
                        failure_details.append(f"{parts[0]}: {test_name}")

            print(f"  DEBUG: Found {len(failure_details)} failure details from summary section")

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
        log_file_preferred = os.path.join(artifact_dir, "pytest-output.log")
        log_file_fallback = os.path.join(artifact_dir, "pytest-output-raw.log")

        log_file = None
        if os.path.exists(log_file_preferred):
            log_file = log_file_preferred
        elif os.path.exists(log_file_fallback):
            log_file = log_file_fallback

        if not log_file:
            continue

        try:
            with open(log_file, encoding="utf-8") as f:
                content = f.read()

            # Fallback: Strip ANSI codes if they're still present (in case sed failed)
            if "\x1b[" in content:
                content = strip_ansi_codes(content)

            lines = content.split("\n")

            # Use the same enhanced logic as in parse_pytest_log for consistency
            version_results = []
            current_test = None

            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                # Handle both same-line and separate-line patterns
                if line.startswith("suite/apps/") and "::" in line:
                    # Check if this line also contains a result (same-line pattern)
                    same_line_result = None
                    if "PASSED" in line or "FAILED" in line or "ERROR" in line:
                        # Extract the test name part
                        test_name_match = re.match(r"^(suite/apps/[^\[]*\[[^\]]+\])", line)
                        if test_name_match:
                            current_test = test_name_match.group(1)

                            # Extract result from the same line
                            if "PASSED" in line:
                                same_line_result = "PASSED"
                            elif "FAILED" in line:
                                same_line_result = "FAILED"
                            elif "ERROR" in line:
                                same_line_result = "ERROR"
                    else:
                        # Test name only, result will be on next lines
                        if line.endswith("]"):
                            current_test = line
                        else:
                            # Handle cases where test name might be cut off
                            bracket_pos = line.rfind("]")
                            if bracket_pos > 0:
                                current_test = line[: bracket_pos + 1]
                            else:
                                current_test = line

                    # Process same-line result immediately
                    if same_line_result and current_test:
                        version_results.append((current_test, same_line_result))

                        if current_test not in test_results:
                            test_results[current_test] = {}
                        test_results[current_test][version] = same_line_result

                        # Reset since we processed this test
                        current_test = None

                    continue

                # Check for separate-line results (original pattern)
                result_patterns = [
                    r"(PASSED|FAILED|ERROR)\s+\[\s*\d+%\]",  # Original pattern
                    r"\[[\d;]*m(PASSED|FAILED|ERROR)\[[\d;]*m.*\[\s*\d+%\]",  # With ANSI codes
                ]

                test_status = None
                for pattern in result_patterns:
                    result_match = re.search(pattern, line)
                    if result_match:
                        test_status = result_match.group(1)
                        break

                if test_status and current_test:
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


def analyze_regressions(test_results: dict[str, dict[str, str]], all_versions: list[str]) -> dict:
    """
    Analyze for potential regressions - tests that fail on 'next' versions but pass on stable versions.

    Returns:
        dict with 'regressions', 'improvements', and 'new_failures' keys
    """
    regressions = []
    improvements = []
    new_failures = []

    # Define version categories
    stable_versions = [v for v in all_versions if v in ["cloud", "previous"]]
    next_versions = [v for v in all_versions if "next" in v.lower()]

    print(
        f"  DEBUG: Analyzing regressions - Stable versions: {stable_versions}, Next versions: {next_versions}"
    )

    for test_name, version_results in test_results.items():
        # Get outcomes for stable vs next versions
        stable_outcomes = {
            v: version_results.get(v, "MISSING") for v in stable_versions if v in version_results
        }
        next_outcomes = {
            v: version_results.get(v, "MISSING") for v in next_versions if v in version_results
        }

        # Skip if we don't have results for both categories
        if not stable_outcomes or not next_outcomes:
            continue

        # Check for regressions (passes on stable, fails on next)
        stable_passing = any(outcome == "PASSED" for outcome in stable_outcomes.values())
        next_failing = any(outcome in ["FAILED", "ERROR"] for outcome in next_outcomes.values())

        if stable_passing and next_failing:
            regressions.append(
                {
                    "test_name": test_name,
                    "stable_results": stable_outcomes,
                    "next_results": next_outcomes,
                    "severity": "HIGH"
                    if all(outcome in ["FAILED", "ERROR"] for outcome in next_outcomes.values())
                    else "MEDIUM",
                }
            )

        # Check for improvements (fails on stable, passes on next)
        stable_failing = any(outcome in ["FAILED", "ERROR"] for outcome in stable_outcomes.values())
        next_passing = any(outcome == "PASSED" for outcome in next_outcomes.values())

        if stable_failing and next_passing:
            improvements.append(
                {
                    "test_name": test_name,
                    "stable_results": stable_outcomes,
                    "next_results": next_outcomes,
                }
            )

        # Check for new failures (only fails on next, no stable data or all stable pass)
        all_stable_pass = (
            all(outcome == "PASSED" for outcome in stable_outcomes.values())
            if stable_outcomes
            else False
        )
        all_next_fail = (
            all(outcome in ["FAILED", "ERROR"] for outcome in next_outcomes.values())
            if next_outcomes
            else False
        )

        if all_stable_pass and all_next_fail:
            new_failures.append(
                {
                    "test_name": test_name,
                    "stable_results": stable_outcomes,
                    "next_results": next_outcomes,
                }
            )

    return {"regressions": regressions, "improvements": improvements, "new_failures": new_failures}


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

        # Add regression analysis
        print("Analyzing for potential regressions...")
        regression_analysis = analyze_regressions(test_results, all_versions)

        # Show regressions first as they're most critical
        if regression_analysis["regressions"]:
            f.write("## 🚨 POTENTIAL REGRESSIONS\n")
            f.write(
                f"**⚠️ Found {len(regression_analysis['regressions'])} tests that pass on stable versions but fail on next versions!**\n\n"
            )

            high_severity = [
                r for r in regression_analysis["regressions"] if r["severity"] == "HIGH"
            ]
            medium_severity = [
                r for r in regression_analysis["regressions"] if r["severity"] == "MEDIUM"
            ]

            if high_severity:
                f.write("### 🔥 HIGH SEVERITY (Fail on ALL next versions)\n")
                for reg in high_severity[:5]:  # Show top 5 high severity
                    test_short_name = reg["test_name"].split("::")[-1]
                    f.write(f"#### 🔍 `{test_short_name}`\n")
                    f.write(
                        f"- **✅ Stable:** {', '.join(f'{v}({s})' for v, s in reg['stable_results'].items())}\n"
                    )
                    f.write(
                        f"- **❌ Next:** {', '.join(f'{v}({s})' for v, s in reg['next_results'].items())}\n\n"
                    )

            if medium_severity:
                f.write("### ⚠️ MEDIUM SEVERITY (Fail on some next versions)\n")
                for reg in medium_severity[:3]:  # Show top 3 medium severity
                    test_short_name = reg["test_name"].split("::")[-1]
                    f.write(f"#### 🔍 `{test_short_name}`\n")
                    f.write(
                        f"- **✅ Stable:** {', '.join(f'{v}({s})' for v, s in reg['stable_results'].items())}\n"
                    )
                    f.write(
                        f"- **❌ Next:** {', '.join(f'{v}({s})' for v, s in reg['next_results'].items())}\n\n"
                    )

        # Show improvements (fixes)
        if regression_analysis["improvements"]:
            f.write("## 🎉 IMPROVEMENTS\n")
            f.write(
                f"Found {len(regression_analysis['improvements'])} tests that were fixed in next versions:\n\n"
            )

            for imp in regression_analysis["improvements"][:3]:  # Show top 3 improvements
                test_short_name = imp["test_name"].split("::")[-1]
                f.write(f"### ✅ `{test_short_name}`\n")
                f.write(
                    f"- **❌ Stable:** {', '.join(f'{v}({s})' for v, s in imp['stable_results'].items())}\n"
                )
                f.write(
                    f"- **✅ Next:** {', '.join(f'{v}({s})' for v, s in imp['next_results'].items())}\n\n"
                )

        # General inconsistencies (less critical but still useful)
        if inconsistent_tests:
            f.write("## 📊 All Version Inconsistencies\n")
            f.write(
                f"Found {len(inconsistent_tests)} tests with different results across versions:\n\n"
            )

            for issue in inconsistent_tests[:5]:  # Reduced to 5 since regressions are shown above
                test_short_name = issue["test_name"].split("::")[
                    -1
                ]  # Get just the test method name

                f.write(f"### 🔍 `{test_short_name}`\n")
                if issue["failed_versions"]:
                    f.write(f"- **❌ Failed on:** {', '.join(sorted(issue['failed_versions']))}\n")
                if issue["passed_versions"]:
                    f.write(f"- **✅ Passed on:** {', '.join(sorted(issue['passed_versions']))}\n")
                f.write("\n")

            if len(inconsistent_tests) > 5:
                f.write(f"... and {len(inconsistent_tests) - 5} more inconsistent tests\n\n")
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
