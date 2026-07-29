from pathlib import Path


WORKFLOW = Path(__file__).parent / "workflows" / "publish.yml"
DRAIN_WORKFLOW = Path(__file__).parent / "workflows" / "drain-splunkbase-publish-queue.yml"
ENQUEUE_WORKFLOW = Path(__file__).parent / "workflows" / "enqueue-splunkbase-publish.yml"
ENQUEUE_ACTION = Path(__file__).parent / "actions" / "enqueue-publish" / "action.yml"


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


def test_skipped_direct_publish_cannot_trigger_release_slack():
    workflow = WORKFLOW.read_text()
    notify = workflow.split("\n  notify-slack:\n", 1)[1]

    assert "needs.publish.result != 'skipped'" in notify
    assert "needs.publish.outputs.return_code != ''" in notify


def test_queue_workflows_execute_self_contained_uv_scripts():
    workflow = DRAIN_WORKFLOW.read_text()
    enqueue_workflow = ENQUEUE_WORKFLOW.read_text()
    enqueue_action = ENQUEUE_ACTION.read_text()
    select = workflow.split("\n  select:\n", 1)[1].split("\n  publish-one:\n", 1)[0]
    publish = workflow.split("\n  publish-one:\n", 1)[1]
    drain_script = (
        Path(__file__).parent / "scripts" / "drain_splunkbase_publish_queue.py"
    ).read_text()
    enqueue_script = (
        Path(__file__).parent / "scripts" / "enqueue_splunkbase_publish.py"
    ).read_text()
    prepare_script = (
        Path(__file__).parent / "actions" / "enqueue-publish" / "prepare_enqueue.py"
    ).read_text()
    notify_script = (
        Path(__file__).parent / "scripts" / "notify_splunkbase_publish_failure.py"
    ).read_text()

    assert "uses: astral-sh/setup-uv" not in workflow
    assert "uses: actions/setup-python@v5" in select
    assert 'python -m pip install "uv==0.12.0"' in select
    assert "uv run .github/scripts/drain_splunkbase_publish_queue.py select" in select
    assert "uv run .github/scripts/drain_splunkbase_publish_queue.py download" in publish
    assert "uv run .github/scripts/drain_splunkbase_publish_queue.py publish" in publish
    assert "uv run .github/scripts/notify_splunkbase_publish_failure.py" in publish
    assert "uses: astral-sh/setup-uv" not in enqueue_workflow
    assert "uses: actions/setup-python@v5" in enqueue_workflow
    assert 'python -m pip install "uv==0.12.0"' in enqueue_workflow
    assert "uv run .github/scripts/enqueue_splunkbase_publish.py" in enqueue_workflow
    assert "uses: astral-sh/setup-uv" not in enqueue_action
    assert "uses: actions/setup-python@v5" in enqueue_action
    assert 'python -m pip install "uv==0.12.0"' in enqueue_action
    assert 'uv run "${{ github.action_path }}/prepare_enqueue.py"' in enqueue_action
    assert "pip install -r" not in select
    assert "backoff" not in select
    assert "packaging" not in select
    assert "requests" not in select
    assert '"backoff>=2.2.1,<3.0.0"' in drain_script
    assert '"requests>=2.32.3,<3.0.0"' in drain_script
    assert '"packaging>=24.2,<26.0"' in drain_script
    assert '"requests>=2.32.3,<3.0.0"' in enqueue_script
    assert "dependencies = []" in prepare_script
    assert '"requests>=2.32.3,<3.0.0"' in notify_script


def test_concurrent_queue_release_creation_waits_for_the_winner():
    action = ENQUEUE_ACTION.read_text()
    release_step = action.split(
        "\n    - name: Ensure durable queue release exists\n",
        1,
    )[1].split("\n    - name: Store immutable connector artifact\n", 1)[0]

    assert "--target main" in release_step
    assert "for delay in 1 2 3 4 5" in release_step
    assert 'if [ "$release_visible" != "true" ]' in release_step
