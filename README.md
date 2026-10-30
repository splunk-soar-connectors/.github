# Splunk> SOAR
Welcome! This repository contains the community health files and workflow templates
for the ```splunk-soar-connectors``` organization.

Please see the ```.github``` folder for the following docs.
 - [Contributing guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/CONTRIBUTING.md)
 - [Partners guide](https://github.com/Splunk-SOAR-Apps/.github/blob/main/.github/PARTNERS.md)
 - [PR template](https://github.com/splunk-soar-connectors/.github/blob/main/.github/pull_request_template.md)
 - [Issue templates](https://github.com/splunk-soar-connectors/.github/tree/main/.github/ISSUE_TEMPLATE)

## Connector release downloads

For each new BaseConnector or SDK version, the shared publish workflow attaches
the built `<repository>.tgz` and `<repository>.tgz.sha256` to that connector's GitHub
release. These are the package produced by the BaseConnector builder or
`soarapps package build` and its SHA-256 checksum, so consumers can download the
built connector directly from its release page. This applies to both direct and
queued Splunkbase publishing.

The package build uses the commit created by semantic-release. Runs that do not
create a new version skip release-asset publication. Retrying the asset job reuses
assets with matching digests and uploads missing files; conflicting or unverifiable
existing assets cause the job to fail without replacing them. Existing releases
can be published manually as described below.

## Publish an existing connector version

In this repository's **Actions** tab, select **Publish existing connector release**
and choose **Run workflow** on `main`. Supply the connector repository name
(for example, `slack`) and its existing release tag (for example, `2.12.0`).

The workflow checks out that exact tag and builds either a BaseConnector or SDK
package. It verifies that the packaged version matches the tag, then attaches
`<repository>.tgz` and `<repository>.tgz.sha256` to the existing GitHub release.
It does not change the app version, create tags/releases, or publish to Splunkbase.

The existing `SEMANTIC_RELEASE_APP_ID` variable and `semantic_release_pk` secret
must be available to this repository, and that App must have `contents:write`
access to the selected connector. The build job has no release-writing credential;
the publish job receives a token scoped to the selected connector after the build.
Old versions still need their locked SDK dependencies or BaseConnector build files
to be available. Rebuilding a version may produce different archive bytes; existing
assets are never replaced. To retry a failed upload, rerun only the failed publish
job so it reuses the original build artifact.

## Connector distribution pipelines

After release assets are published, both the automatic and existing-version
workflows start a soar-services pipeline on `main` for `playground`, wait for it
to succeed, then start and monitor `staging`. A failed, canceled, blocked, or
timed-out playground run prevents staging from starting.

Distribution requires the packaged connector manifest's publisher to be exactly
`Splunk`. The action downloads the release archive, verifies GitHub's SHA-256,
and checks its manifest before calling GitLab. `Splunk Community`, other
publishers, and missing publisher values are reported as skipped; they create
no distribution pipeline. Invalid or ambiguous archives fail the check. Normal
release-asset and Splunkbase publishing continue for other publishers.

The trigger job runs on the existing CodeBuild runner
and uses its `GITLAB_API_TOKEN` environment variable. It sends the public release
archive URL, the SHA-256 digest reported by GitHub for that asset, and the target
environment as pipeline inputs. No additional per-connector secret is required.

Each job prints pipeline statuses and adds a pipeline link and result to the
GitHub job summary. It waits up to 25 minutes and succeeds only when GitLab
reports pipeline success. Failed trigger requests fail the GitHub job and are
not automatically retried; only status reads use bounded retries. After an
uncertain network failure, check GitLab before retrying to avoid duplicate runs.
The trigger job can be rerun without rebuilding or republishing release assets.

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
