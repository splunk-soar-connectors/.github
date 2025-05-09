
## Automated Checks
By default we provide various automated checks you can leverage to test your changes automatically. These checks will be run whenever you push new commits to your pull request branch. The overall pass/fail result will appear as a green checkmark or red "x" to the right of commit in the pull request page. To view the detailed report you can do **ANY** of the following:

- Click the checkmark or "x" and then click the "Details" link. **OR**
- Click the "Checks" tab at the top of the pull request. **OR**
- Click the "Details" link next to the list of checks that shows up at the bottom of the pull request. If the tests passed, this list will be hidden, so you will first need to click the "Show all checks" link.

The checks performed are the following:

(Pre-commit checks that can be run with a commit locally or with `pre-commit run --all-files`)

- **Commit Message Validation**: Ensures commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/#summary) format
- **Code Quality Checks**:
  - Python linting and formatting with [Ruff](https://astral.sh/ruff)
  - Django template linting and formatting with [djlint](https://www.djlint.com/)
  - Markdown formatting with [mdformat](https://mdformat.readthedocs.io/en/stable/)
  - Security scanning with [semgrep](https://semgrep.dev/index.html)
- **General Checks**:
  - Merge conflict detection
  - End-of-file fixing
  - Trailing whitespace cleanup
  - Requirements.txt formatting
  - JSON and YAML validation
- **Splunk-specific Checks**:
  - Documentation building
  - Copyright verification
  - App dependency packaging
  - Release notes validation
  - Static tests

Additionally, our CI/CD pipeline runs these checks on every push, these can be viewed on the pull request:

- **Pre-commit Checks**:
  - Listed above
- **Security Scans**:
  - Semgrep static analysis
  - Detect secrets for credential scanning
  - Additional vulnerability scanning of dependencies
- **Test Coverage**: Measures code coverage of tests (this will fail until support can help add tests for new apps and changes)
- **Compilation**: Verifies app compiles correctly
- **Build**: Creates app package
- **Sanity Tests**: Validates app functionality across multiple environments:
  - Current version
  - Next version
  - Previous version
  - Cloud environment
  - RHEL environment
- **Integration Tests**: Runs comprehensive integration test suite
