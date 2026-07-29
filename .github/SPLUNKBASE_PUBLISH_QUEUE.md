# Splunkbase publish queue

This runbook and the queue implementation were written by Codex.

Splunkbase permits 20 upload attempts per hour for each publishing user, and every
multipart upload attempt counts. Connector builds remain parallel, but this queue
serializes the upload POSTs made by the shared SOAR connector user.

## Safety properties

The worker starts at most one upload every five minutes and no more than 12 uploads
during the preceding hour. It records a slot before making the POST. A worker restart
therefore cannot reset the budget. Read-only Splunkbase requests retain bounded retries,
but multipart upload POSTs have no automatic retries.

Queue items are GitHub issues in this repository. Artifacts are uniquely named assets on
the `splunkbase-publish-queue` prerelease. Both contain only public connector and
operational metadata; credentials remain in GitHub Actions secrets. The deduplication
key is publishing-user alias, repository, and connector version.

## Configuration

Before enabling the queue, configure these Actions secrets on
`splunk-soar-connectors/.github`:

- `SPLUNKBASE_USER`
- `SPLUNKBASE_PASSWORD`
- `SLACK_INTERNAL_TOKEN`
- `SLACK_COMMUNITY_TOKEN`

Confirm the existing channel variables are available:

- `SLACK_INTERNAL_CHANNEL_ID`
- `SLACK_COMMUNITY_CHANNEL_ID`
- `SEND_RELEASE_MESSAGE`

The enqueue job uses the existing `SEMANTIC_RELEASE_APP_ID` organization variable and
the `SEMANTIC_RELEASE_PK` secret already passed by connector caller workflows. The App
needs `contents:write` on `splunk-soar-connectors/.github`; it does not need issue,
Actions, check, or status permissions.

Keep `SPLUNKBASE_PUBLISH_QUEUE_ENABLED` unset or `false` while configuring and testing.
The reusable workflow continues to publish directly in that state, with one upload POST
per job. Set this variable to `true` for only the five approved canary repositories
first. After the canary passes, expose the same variable to all connector repositories
and this central repository.

## Canary

Enqueue five approved connector versions together. Verify:

1. Five open issues appear with `splunkbase-publish` and `splunkbase-queued`.
2. The state issue records no starts less than five minutes apart.
3. Each successful issue closes with `splunkbase-published`.
4. Metrics and Slack notifications run only after the worker confirms publication.
5. Splunkbase logs show the stable `Splunk-SOAR-Connector-Publisher/1.0` User-Agent plus
   repository, version, source run, and source attempt fields.

Do not canary CrowdSec until its publishing-user invitation is accepted. Do not use
Microsoft OneDrive v2 as a canary.

## Recovery

An active item carries a 15-minute lease. If a worker dies, the item becomes eligible
again after the lease. The next worker checks whether the version exists before
reserving another upload slot.

HTTP 429 requeues the item after `Retry-After` plus 30 seconds and makes no second POST.
HTTP 401 or 403 and definitive validation failures leave the issue open with
`splunkbase-blocked`. An ambiguous timeout, reset, or server error triggers GET-only
reconciliation; it is requeued only when the candidate version is still absent.

To retry a corrected blocked item, preserve its JSON body, replace
`splunkbase-blocked` with `splunkbase-queued`, and set `not_before` to the current UTC
time. Do not rerun a POST manually under the same user while the queue is enabled,
because manual attempts are invisible to the persisted budget.

To disable the queue, set `SPLUNKBASE_PUBLISH_QUEUE_ENABLED` to `false`. Wait for any
active worker to finish before using direct publication. Do not operate both paths
simultaneously under the same Splunkbase user.
