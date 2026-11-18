# Splunkbase connector publishing

Splunk SOAR connector releases use a centralized queue in
`splunk-soar-connectors/.github` to publish to Splunkbase. The queue provides durable
publication state, enforces the upload limit for the shared publishing identity, and
prevents an uncertain upload result from causing a duplicate submission.

## Publishing flow

Each connector release workflow stores the immutable connector artifact and sends a `repository_dispatch` event to the central
repository. The `enqueue-splunkbase-publish.yml` workflow handles the event and creates
or updates the GitHub issue that represents the publication.

The `drain-splunkbase-publish-queue.yml` workflow is the central worker. It runs on a
schedule and can also be started manually with `workflow_dispatch`. A concurrency group
allows only one worker to drain the queue at a time.

For read-only troubleshooting, dispatch the same workflow with
`diagnose_issue_number` set to the queue issue number. This mode authenticates,
reads the queue item, app metadata, and any saved package validation result, and
checks the release response shape. It
skips the worker, package uploads, queue updates, and notifications. Logs contain
the endpoint, HTTP status, request ID when available, response field names, and
publisher exception details. Credentials and response bodies are excluded.

```mermaid
flowchart TB
    A["Connector release builds an immutable package"] --> B["Store artifact and enqueue publication"]
    B --> C["Central worker selects eligible work"]
    C --> D["Read saved draft checkpoint"]
    D --> E{"Package already registered?"}
    E -- "No" --> F["Reserve upload budget and checkpoint mutations"]
    F --> G["Create or reuse app, upload to S3, register package"]
    E -- "Yes" --> H["Poll saved package validation"]
    G --> H
    H --> I["Verify authenticated app identity and candidate version"]
    I --> J["Update metadata and publish release or submit draft"]
    J --> K{"Exact version publicly visible?"}
    K -- "Yes" --> L["Close queue item and send metrics and announcements"]
    K -- "No" --> M["Retain checkpoint for a later verification window"]
    M --> D
    F -. "Unknown draft creation or registration outcome" .-> N["Hold for operator reconciliation"]
```

## Queue model

Each publication is represented by:

- A GitHub issue containing the connector, version, source run, current state, and
  non-secret Splunkbase identifiers.
- An immutable connector artifact stored on the `splunkbase-publish-queue` prerelease.
- A deduplication key composed of the publishing-user alias, repository, and connector
  version.

The queue selects eligible queued or expired-active work before verification work.
Within each group it uses the oldest `enqueued_at` timestamp and then the issue number
as a stable tie-breaker. A drain run excludes every issue it has already selected, so
each eligible issue receives at most one processing attempt per run. Publication
credentials remain in GitHub Actions secrets and are not stored in issues or release
assets.

## Upload budget

Splunkbase permits 20 upload attempts per hour for each publishing user, and every
package upload attempt counts. The queue starts at most one upload every three
minutes and no more than 20 during the preceding hour.

Each worker run drains eligible work for at most 50 minutes or 20 started upload
attempts, whichever comes first. The workflow has a separate 60-minute hard timeout;
the internal deadline leaves margin for cleanup and notification. It records an upload
slot before making the POST, so a restart cannot reset the budget. Read-only Splunkbase
requests retain bounded retries, but package upload POSTs have no automatic retries.
HTTP 429 responses schedule another queue attempt after `Retry-After` plus 30 seconds.
Ambiguous results retain the draft checkpoint. Unknown draft creation or package
registration outcomes require operator reconciliation before either operation repeats.

Manual uploads must not use the same publishing identity outside the queue
because they are not represented in the persisted budget.

## Publication verification

After Splunkbase accepts an upload, the queue persists the package and request IDs and
checks for the release immediately. It checks every 10 seconds for up to five minutes.
If publication is still not confirmed, the issue remains in verification and a later
worker resumes from the saved checkpoint. After publication is requested, visibility
checks use GET requests only. Each completed five-minute verification window
increments the persisted `verification_rechecks` counter. The first two unsuccessful
rechecks remain non-terminal; the third blocks the issue with a sanitized reason. The
escalation never authorizes another package upload.

Release metrics and the standard Slack release announcement are sent only after the
connector version is confirmed on Splunkbase.

For SDK apps, metrics compare the manifest in the immutable queued package with a
manifest generated from the previous semantic-release tag. Metrics and Slack
notifications are attempted independently so one failure does not suppress the other.

## Failure handling

| Condition | Queue behavior | Notification |
| --- | --- | --- |
| HTTP 429 | Requeue after `Retry-After` plus 30 seconds | None |
| Ambiguous timeout or server response | Retain checkpoint; hold unknown creation/registration outcomes | None |
| Validation still pending or response unreadable, before three rechecks | Keep the issue in verification | None |
| Three unsuccessful five-minute verification rechecks | Block the issue for human review | Internal Slack warning |
| HTTP 401 or 403 | Block the issue for human review | Internal Slack warning |
| Definitive validation rejection | Block the issue for human review | Internal Slack warning |
| A newer connector version is already on Splunkbase | Delete the queued asset and close the blocked issue | None |
| Unexpected worker exception | Workflow fails | Internal Slack warning with the worker run |
| Cancelled or timed-out drain workflow | No queue mutation | Internal Slack warning with the worker run |

Publication checkpoints delay another worker for 15 minutes while work is active.
If a worker stops, the next eligible worker resumes the saved operation and identifiers.

The queue issue and internal blocked-publication warning include the connector, version,
specific failure reason, and worker-run link. Request and package IDs are included only
when the failure occurred after Splunkbase received an upload. The warning is independent
of `SEND_RELEASE_MESSAGE`, which controls standard release announcements.
The worker sends no transient verification-retry warning. A separate `workflow_run`
observer reports cancelled or timed-out drain runs because a cancelled worker may not
get a chance to run its own exception handler.

Blocked issues remain open until an operator resolves the cause and explicitly
authorizes another queue attempt, unless a later drain finds a newer connector version
on Splunkbase and evicts the stale item automatically. An operator must not retry the
package upload directly.

## Configuration

### Splunkbase v2 draft publishing (PAPP-38509)

All connector publications use the central queue and the v2 draft API. Publishing
has one path and requires no feature flags.

The publisher logs in with a JSON-encoded POST, caches the JWT until its expiry,
and refreshes once after an explicit authentication rejection. It creates a draft
for a new listing, obtains a presigned S3 POST, uploads the package without
Splunkbase credentials, registers its filename and S3 key, and polls validation.
For existing listings, it reuses the numeric app ID.

Validation can return `Pass` with empty `details`. The worker resolves the saved
app ID through the authenticated app endpoint and requires a matching numeric app
ID, connector GUID, and candidate release version before updating metadata. It
waits when the release is not yet listed, retains the saved package ID, and uses
the release version as the PATCH identifier when editor metadata omits a numeric
release ID. Conflicting validation identity fields still block publication.
Public app lookups explicitly request `product=soar` and `include=releases`.

After validation passes, the worker updates app metadata and release notes. New
drafts must have no missing `/required_fields/` before `/submit/`. Existing apps
publish the validated release with a release PATCH. Both paths require the exact
connector GUID and version to appear in unauthenticated public app search before
closing the queue item, deleting the artifact, or sending metrics and announcements.
Private validation success alone does not establish public availability.

#### Listing content

All documentation is read from the immutable package as Markdown. Only regular
files at the package root are read; the worker does not extract links or paths.

| Splunkbase field | Package source, in priority order |
| --- | --- |
| `description` | Connector manifest description |
| `documentation_source` | `details.md`; `## Details`; `manual_readme_content.md`; `README.md` |
| `installation_source` | `installation.md`; `## Installation` |
| `troubleshooting_source` | `troubleshooting.md`; `## Troubleshooting` |
| `contact_notes_source` | `contact.md`; `## Contact` |
| `contact_email` | Explicit manifest `contact_email`, when available |
| `support` | Explicit manifest `developer` or `not_supported`; all other values are omitted |

Section headings are matched without regard to case. Sections come from
`manual_readme_content.md` when present, or `README.md` otherwise. Empty or absent
values are omitted, preserving curated listing content. Existing contact email and
support are preserved when already set on the listing. Splunkbase manages
`splunk`, `cisco`, and `premium` support; publishing never infers support from the
connector's publisher name. A tab body longer than
20,000 characters blocks publication with a request for a dedicated tab file.
Connector category strings are not converted to Splunkbase numeric category IDs.
Missing categories, compatibility, contact information, or other required listing
metadata are reported for the listing owner to complete before resuming.

#### Durable recovery

The queue stores `publication_flow=draft_v2`, the pending operation, app/package/
release IDs, the uploaded S3 object key, and whether publication was requested.
It checkpoints before each mutation and after accepting its result. S3 policy,
signature, session token, presigned URL, and JWT are never stored in the queue.

| Interrupted operation | Recovery |
| --- | --- |
| Draft creation, app ID not persisted | Hold for App Hosting to identify the created draft; do not repeat creation |
| S3 upload before package registration | Obtain a fresh presigned grant and upload an unregistered object |
| Package registration, package ID not persisted | Hold for App Hosting to identify the package; do not register again |
| Accepted package validation | Resume polling the saved package ID |
| Metadata or release PATCH | Repeat the same values from the immutable package |
| Draft submission | Read draft state; resume if submitted, or hold while its outcome remains unknown |
| Public visibility | Read public search without repeating uploads or submission |

HTTP 429 retains identifiers and schedules a retry after `Retry-After` plus
30 seconds. A rejected registration can reuse the uploaded object. A rejected
or expired S3 grant never triggers registration. HTTP 409, permanent permission
denial, validation failure, and incomplete metadata block the item for review.
The existing upload budget is reserved before each S3 attempt; the API's monthly
byte quota is also respected through HTTP 429.

To resume a held item, reconcile its IDs with App Hosting, update the queue issue's
`verification` checkpoint with the confirmed app or package ID, and clear the
resolved `pending_operation`. Preserve the immutable artifact and flow marker.
Return the item to verification only after confirming the failed operation's outcome.
Do not delete a draft or retry a registration to bypass an uncertain result.

Checkpoints from the removed publishing implementation are blocked for operator
reconciliation. They cannot be replayed as draft uploads. CrowdSec queue item #545
is currently held with such a checkpoint; preserve its history and verify any prior
upload outcome before preparing new work.

#### Validation

The [Publishing tests workflow](workflows/publishing-tests.yml) runs repository
hooks and publisher/queue tests on the PR revision using a read-only token.
Its tests cover request formats, authentication refresh, S3 credential isolation,
validation versus publication, durable checkpoints, quota handling, and recovery.

The API client supports `SPLUNKBASE_ENVIRONMENT=STAGE` with matching staging API
and login hosts. Staging validation requires a publishing identity, test listings,
and an isolated staging queue. Exercise BaseConnector and SDK packages, listing
tabs, restart recovery, and final public visibility. No staging publishing account
or test listing is currently configured.
New listings are created with `draft: "true"` as required by the API.

Contract references: [migration guide](https://cisco-my.sharepoint.com/:w:/p/jramatow/IQBsArEQiaYvTY9ZkeDc_26RAZtnZyX7zQ_krNuKjGM73Dk?e=e8rxGb),
[v2 API documentation](https://stage.splunkbase.splunk.com/api-docs-v2/),
[OpenAPI schema](https://stage.splunkbase.splunk.com/schema/), and
[MP-510 metadata mapping](https://splunk.atlassian.net/browse/MP-510?focusedCommentId=20905715).

The central repository uses the following Actions secrets:

- `SPLUNKBASE_USER`
- `SPLUNKBASE_PASSWORD`
- `SLACK_INTERNAL_TOKEN`
- `SLACK_COMMUNITY_TOKEN`

Slack behavior uses the following Actions variables:

- `SLACK_INTERNAL_CHANNEL_ID`
- `SLACK_COMMUNITY_CHANNEL_ID`
- `SEND_RELEASE_MESSAGE`

The connector release workflow uses `SEMANTIC_RELEASE_APP_ID` and
`SEMANTIC_RELEASE_PK` to obtain a token for storing the queue artifact and sending the
enqueue event. The GitHub App requires `contents:write` on
`splunk-soar-connectors/.github`. The central workflows use their repository-scoped
`GITHUB_TOKEN` with `issues:write` to manage queue issues.

Existing callers may continue passing the reusable release workflow’s Splunkbase
and Slack secrets, but they are optional and unused there. The central worker owns
publishing credentials, metrics, and release notifications.

## Tracing

Splunkbase uploads use the `Splunk-SOAR-Connector-Publisher/1.0` User-Agent and include
the connector repository, version, source workflow run, and source attempt. The queue
issue records package and request IDs when Splunkbase provides them.

## Pausing publishing

Disable the **Drain Splunkbase publish queue** workflow in GitHub Actions and let any
active run finish. Connector releases continue enqueueing immutable artifacts.
Re-enable the workflow to resume; direct publication is no longer supported.

_This documentation was written by Codex._
