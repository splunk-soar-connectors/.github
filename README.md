# Splunk> SOAR
Welcome! This repository contains the community health files and workflow templates
for the ```splunk-soar-connectors``` organization.

Please see the ```.github``` folder for the following docs.
 - [Contributing guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/CONTRIBUTING.md)
 - [Partners guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/PARTNERS.md)
 - [PR template](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md)
 - [Issue templates](https://github.com/splunk-soar-connectors/.github/tree/main/.github/ISSUE_TEMPLATE)

## SDK connector release downloads

For each new SDK connector version, the shared publish workflow attaches the built
`<repository>.tgz` and `<repository>.tgz.sha256` to that connector's versioned GitHub
release. These are the package produced by `soarapps package build` and its SHA-256
checksum, so consumers can download the built connector directly from its release
page. This applies to both direct and queued Splunkbase publishing.

The package build uses the commit created by semantic-release. Runs that do not
create a new version skip release-asset publication. Retrying the asset job reuses
assets with matching digests and uploads missing files; conflicting or unverifiable
existing assets cause the job to fail without replacing them. Existing releases
are not backfilled.

## Connector contract summaries

The organization-wide [PR workflow](.github/workflows/push.yml) compares the base
and PR versions of each connector's asset parameters, action inputs, and action
outputs. It reads BaseConnector app JSON directly and generates the canonical
manifest for SDK connectors. A PR with no contract changes receives one
informational summary. Contract changes receive a warning summary as a normal PR
comment. The workflow keeps at most one summary comment: it leaves identical
content alone and deletes and replaces the comment when the summary changes. The
comment does not block a PR.

The comparison job runs with a read-only token. A separate reporting job runs
trusted code with permission to write the PR comment.
