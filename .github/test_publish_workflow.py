from pathlib import Path


WORKFLOW = Path(__file__).parent / "workflows" / "publish.yml"
DRAIN_WORKFLOW = Path(__file__).parent / "workflows" / "drain-splunkbase-publish-queue.yml"


def test_enqueue_uses_the_commit_that_created_the_artifact():
    workflow = WORKFLOW.read_text()
    build = workflow.split("\n  build:\n", 1)[1].split("\n  publish:\n", 1)[0]
    enqueue = workflow.split("\n  enqueue-publish:\n", 1)[1].split("\n  metrics:\n", 1)[0]

    assert "commit_sha: ${{ steps.source.outputs.commit_sha }}" in build
    assert 'echo "commit_sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in build
    assert "commit_sha: ${{ needs.build.outputs.commit_sha }}" in enqueue
    assert "actions/checkout" not in enqueue


def test_drain_notifies_internal_slack_only_for_terminal_blocked_items():
    workflow = DRAIN_WORKFLOW.read_text()
    notification = workflow.split(
        "\n      - name: Notify internal Slack of blocked publication\n",
        1,
    )[1]

    assert "if: always() && steps.publish.outputs.queue_status == 'blocked'" in notification
    assert "SLACK_INTERNAL_TOKEN: ${{ secrets.SLACK_INTERNAL_TOKEN }}" in notification
    assert "QUEUE_ISSUE_URL:" in notification
    assert "SPLUNKBASE_REQUEST_ID:" in notification
    assert "SPLUNKBASE_PACKAGE_ID:" in notification
    assert "FAILURE_REASON:" in notification
    assert "queue_status == 'verifying'" not in notification
    assert "queue_status == 'deferred'" not in notification
    assert "queue_status == 'rate_limited'" not in notification
