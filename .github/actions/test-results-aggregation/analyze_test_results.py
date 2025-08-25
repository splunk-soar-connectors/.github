#!/usr/bin/env python3
"""
Sanity Test Results Aggregation Script

This script analyzes sanity test results from multiple environments (6 different Phantom environments)
and provides detailed comparison and summary of test outcomes across environments.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class TestResult:
    """Represents a single test result"""

    test_name: str
    status: str  # PASSED, FAILED, SKIPPED, ERROR
    environment: str
    duration: Optional[float] = None
    error_message: Optional[str] = None
    failure_reason: Optional[str] = None


@dataclass
class EnvironmentSummary:
    """Summary of test results for an environment"""

    environment: str
    total_tests: int
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: Optional[float] = None


class TestResultsAnalyzer:
    def __init__(self, app_repo: str):
        self.app_repo = app_repo
        self.test_results: list[TestResult] = []
        self.environment_summaries: dict[str, EnvironmentSummary] = {}

    def parse_pytest_output(self, output_file: Path, environment: str) -> list[TestResult]:
        """Parse pytest output from a log file"""
        results = []

        if not output_file.exists():
            print(f"Warning: Test output file not found for {environment}: {output_file}")
            return results

        content = output_file.read_text()

        # Parse pytest output using various patterns
        results.extend(self._parse_pytest_json_report(content, environment))
        if not results:
            results.extend(self._parse_pytest_verbose_output(content, environment))

        return results

    def _parse_pytest_json_report(self, content: str, environment: str) -> list[TestResult]:
        """Parse pytest JSON report format"""
        results = []
        try:
            # Look for JSON report in content
            json_match = re.search(r'\{.*"tests".*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                for test in data.get("tests", []):
                    result = TestResult(
                        test_name=test.get("nodeid", ""),
                        status=test.get("outcome", "UNKNOWN").upper(),
                        environment=environment,
                        duration=test.get("duration"),
                        error_message=test.get("call", {}).get("longrepr")
                        if test.get("outcome") == "failed"
                        else None,
                    )
                    results.append(result)
        except (json.JSONDecodeError, KeyError):
            pass  # Fall back to text parsing

        return results

    def _parse_pytest_verbose_output(self, content: str, environment: str) -> list[TestResult]:
        """Parse pytest verbose text output"""
        results = []

        # Pattern for test results: test_name PASSED/FAILED/SKIPPED
        test_pattern = r"(\S+::\S+(?:::\S+)*)\s+(PASSED|FAILED|SKIPPED|ERROR)(?:\s+\[(\d+)%\])?"

        for match in re.finditer(test_pattern, content):
            test_name = match.group(1)
            status = match.group(2)

            # Look for failure details
            failure_reason = None
            if status == "FAILED":
                # Try to extract failure reason from nearby lines
                failure_pattern = rf"{re.escape(test_name)}.*?FAILED.*?\n(.*?)(?=\n\S|\n=|\Z)"
                failure_match = re.search(failure_pattern, content, re.DOTALL)
                if failure_match:
                    failure_reason = failure_match.group(1).strip()

            result = TestResult(
                test_name=test_name,
                status=status,
                environment=environment,
                failure_reason=failure_reason,
            )
            results.append(result)

        return results

    def load_test_results(self):
        """Load test results from all artifact files"""
        test_results_dir = Path("test-results")

        if not test_results_dir.exists():
            print(f"Error: Test results directory not found: {test_results_dir}")
            sys.exit(1)

        # Environment mapping for sanity tests only
        environments = {
            "sanity-next_ol8": "next_ol8",
            "sanity-next_ol9": "next_ol9",
            "sanity-next_amzn2023": "next_amzn2023",
            "sanity-previous": "previous",
            "sanity-cloud": "cloud",
            "sanity-cloud_next": "cloud_next",
        }

        for artifact_name, env_name in environments.items():
            output_file = test_results_dir / f"{artifact_name}-output.txt"
            results = self.parse_pytest_output(output_file, env_name)
            self.test_results.extend(results)

        print(f"Loaded {len(self.test_results)} test results from {len(environments)} environments")

    def generate_environment_summaries(self):
        """Generate summary statistics for each environment"""
        env_results = defaultdict(list)

        for result in self.test_results:
            env_results[result.environment].append(result)

        for env, results in env_results.items():
            total = len(results)
            passed = sum(1 for r in results if r.status == "PASSED")
            failed = sum(1 for r in results if r.status == "FAILED")
            skipped = sum(1 for r in results if r.status == "SKIPPED")
            errors = sum(1 for r in results if r.status == "ERROR")

            self.environment_summaries[env] = EnvironmentSummary(
                environment=env,
                total_tests=total,
                passed=passed,
                failed=failed,
                skipped=skipped,
                errors=errors,
            )

    def print_environment_status_table(self):
        """Print a table showing pass/fail status per environment"""
        print("\n" + "=" * 80)
        print("TEST RESULTS SUMMARY BY ENVIRONMENT")
        print("=" * 80)

        print(
            f"{'Environment':<15} {'Total':<6} {'Passed':<7} {'Failed':<7} {'Skipped':<8} {'Errors':<7} {'Status':<10}"
        )
        print("-" * 80)

        overall_status = "PASS"

        for env in ["next_ol8", "next_ol9", "next_amzn2023", "previous", "cloud", "cloud_next"]:
            if env in self.environment_summaries:
                summary = self.environment_summaries[env]
                status = "PASS" if summary.failed == 0 and summary.errors == 0 else "FAIL"
                if status == "FAIL":
                    overall_status = "FAIL"

                print(
                    f"{env:<15} {summary.total_tests:<6} {summary.passed:<7} {summary.failed:<7} {summary.skipped:<8} {summary.errors:<7} {status:<10}"
                )
            else:
                print(
                    f"{env:<15} {'N/A':<6} {'N/A':<7} {'N/A':<7} {'N/A':<8} {'N/A':<7} {'NO DATA':<10}"
                )
                overall_status = "FAIL"  # Missing data counts as failure

        print("-" * 80)
        print(f"OVERALL STATUS: {overall_status}")

        return overall_status == "PASS"

    def analyze_test_differences(self):
        """Analyze differences in test results between environments"""
        print("\n" + "=" * 80)
        print("DETAILED TEST RESULT DIFFERENCES")
        print("=" * 80)

        # Group results by test name
        test_by_name = defaultdict(dict)

        for result in self.test_results:
            test_by_name[result.test_name][result.environment] = result

        # Find tests that have different results across environments
        inconsistent_tests = []

        for test_name, env_results in test_by_name.items():
            statuses = set(result.status for result in env_results.values())
            if len(statuses) > 1:  # Different results across environments
                inconsistent_tests.append((test_name, env_results))

        if inconsistent_tests:
            print(
                f"\nFound {len(inconsistent_tests)} tests with different results across environments:\n"
            )

            for test_name, env_results in inconsistent_tests:
                print(f"Test: {test_name}")
                for env in [
                    "next_ol8",
                    "next_ol9",
                    "next_amzn2023",
                    "previous",
                    "cloud",
                    "cloud_next",
                ]:
                    if env in env_results:
                        result = env_results[env]
                        status_info = result.status
                        if result.failure_reason:
                            status_info += f" ({result.failure_reason[:100]}...)"
                        print(f"  {env:<15}: {status_info}")
                print()
        else:
            print("\nNo test result inconsistencies found across environments.")

        # Show environment-specific failures
        self._show_environment_failures()

        return len(inconsistent_tests) == 0

    def _show_environment_failures(self):
        """Show tests that failed in specific environments"""
        print("\nENVIRONMENT-SPECIFIC FAILURES:")
        print("-" * 50)

        env_failures = defaultdict(list)

        for result in self.test_results:
            if result.status in ["FAILED", "ERROR"]:
                env_failures[result.environment].append(result)

        for env in ["next_ol8", "next_ol9", "next_amzn2023", "previous", "cloud", "cloud_next"]:
            if env in env_failures:
                failures = env_failures[env]
                print(f"\n{env} ({len(failures)} failures):")
                for failure in failures:
                    print(f"  - {failure.test_name}: {failure.status}")
                    if failure.failure_reason:
                        print(f"    Reason: {failure.failure_reason[:200]}...")
            else:
                print(f"\n{env}: No failures")

    def analyze(self) -> bool:
        """Run complete sanity test analysis and return True if all tests passed"""
        print(f"Analyzing sanity test results for {self.app_repo} across 6 environments")

        self.load_test_results()
        self.generate_environment_summaries()

        status_ok = self.print_environment_status_table()
        differences_ok = self.analyze_test_differences()

        return status_ok and differences_ok


def main():
    if len(sys.argv) != 2:
        print("Usage: python analyze_test_results.py <app_repo>")
        sys.exit(1)

    app_repo = sys.argv[1]
    analyzer = TestResultsAnalyzer(app_repo)

    try:
        success = analyzer.analyze()
        if not success:
            print(
                "\n❌ Sanity test analysis found issues - some tests failed or have inconsistencies across environments"
            )
            sys.exit(1)
        else:
            print("\n✅ All sanity tests passed across all 6 environments")
    except Exception as e:
        print(f"Error during analysis: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
