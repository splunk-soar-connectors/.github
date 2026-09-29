# Splunk> SOAR
Welcome! This repository contains the community health files and workflow templates
for the ```splunk-soar-connectors``` organization.

Please see the ```.github``` folder for the following docs.
 - [Contributing guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/CONTRIBUTING.md)
 - [Partners guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/PARTNERS.md)
 - [PR template](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md)
 - [Issue templates](https://github.com/splunk-soar-connectors/.github/tree/main/.github/ISSUE_TEMPLATE)

## Connector contract summaries

The reusable [contract workflow](.github/workflows/contract.yml) compares the base and
PR versions of each connector's asset parameters, action inputs, and action outputs.
It reads BaseConnector app JSON directly and generates the canonical manifest for
SDK connectors. A PR with no contract changes receives one informational summary.
Contract changes receive a warning summary as a normal PR comment. The workflow
keeps at most one summary comment: it leaves identical content alone and deletes
and replaces the comment when the summary changes. The comment does not block a PR.

The [caller workflow](.github/workflow-templates/call-contract.yml) runs on
`pull_request_target`; its contract comparison job has a read-only token and its
reporting job runs only trusted code. The
[batch change](.github/batch_changes/migrations/003-contract-summary-check.yaml)
installs the caller in connector repositories.
