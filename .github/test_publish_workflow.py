from pathlib import Path


WORKFLOW = Path(__file__).parent / "workflows" / "publish.yml"
PUSH_WORKFLOW = Path(__file__).parent / "workflows" / "push.yml"
DRAIN_WORKFLOW = Path(__file__).parent / "workflows" / "drain-splunkbase-publish-queue.yml"
WORKER_FAILURE_WORKFLOW = (
    Path(__file__).parent / "workflows" / "notify-splunkbase-publish-queue-worker.yml"
)
ENQUEUE_WORKFLOW = Path(__file__).parent / "workflows" / "enqueue-splunkbase-publish.yml"
ENQUEUE_ACTION = Path(__file__).parent / "actions" / "enqueue-publish" / "action.yml"
NOTIFY_ACTION = Path(__file__).parent / "actions" / "notify-slack" / "action.yml"
NOTIFY_SCRIPT = Path(__file__).parent / "actions" / "notify-slack" / "notify_slack.py"
QUEUE_WORKER = Path(__file__).parent / "scripts" / "drain_splunkbase_publish_queue_worker.py"
WORKER_FAILURE_NOTIFIER = Path(__file__).parent / "scripts" / "notify_splunkbase_publish_worker.py"


def test_semantic_release_uses_compatible_conventional_commits_preset():
    for workflow_path in (WORKFLOW, PUSH_WORKFLOW):
        workflow = workflow_path.read_text()

        assert "conventional-changelog-conventionalcommits@9.3.1" in workflow


def test_sdk_pytest_allows_only_no_tests_collected():
    workflow = PUSH_WORKFLOW.read_text()
    pytest_step = workflow.split("\n      - name: Run pytest\n", 1)[1].split(
        "\n  pre-commit:\n", 1
    )[0]

    assert "set +e" in pytest_step
    assert "pytest_exit_code=$?" in pytest_step
    assert 'if [ "$pytest_exit_code" -eq 5 ]; then' in pytest_step
    assert 'exit "$pytest_exit_code"' in pytest_step


def test_enqueue_uses_the_commit_that_created_the_artifact():
    workflow = WORKFLOW.read_text()
    build = workflow.split("\n  build:\n", 1)[1].split("\n  publish:\n", 1)[0]
    enqueue = workflow.split("\n  enqueue-publish:\n", 1)[1].split("\n  metrics:\n", 1)[0]

    assert "commit_sha: ${{ steps.source.outputs.commit_sha }}" in build
    assert 'echo "commit_sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in build
    assert "commit_sha: ${{ needs.build.outputs.commit_sha }}" in enqueue
    assert "actions/checkout" not in enqueue


def test_drain_notifies_internal_slack_only_for_terminal_blocked_items():
    worker = QUEUE_WORKER.read_text()

    assert 'elif queue_status == "blocked":' in worker
    assert "send_blocked_notification(item, outputs)" in worker
    assert '"QUEUE_ISSUE_URL":' in worker
    assert '"SPLUNKBASE_REQUEST_ID":' in worker
    assert '"SPLUNKBASE_PACKAGE_ID":' in worker
    assert '"FAILURE_REASON":' in worker
    assert 'queue_status == "verifying"' not in worker
    assert 'queue_status == "deferred"' not in worker
    assert 'queue_status == "rate_limited"' not in worker


def test_drain_is_bounded_before_workflow_timeout_and_to_twenty_upload_attempts():
    workflow = DRAIN_WORKFLOW.read_text()
    worker = QUEUE_WORKER.read_text()

    assert "timeout-minutes: 60" in workflow
    assert "MAX_RUN_SECONDS = 50 * 60" in worker
    assert "MAX_UPLOAD_ATTEMPTS = 20" in worker
    assert "attempts_started < MAX_UPLOAD_ATTEMPTS" in worker


def test_drain_fairness_and_worker_failure_alerts_are_wired():
    queue = (Path(__file__).parent / "utils" / "publish_queue.py").read_text()
    publisher = (
        Path(__file__).parent / "scripts" / "drain_splunkbase_publish_queue.py"
    ).read_text()
    worker = QUEUE_WORKER.read_text()
    workflow = WORKER_FAILURE_WORKFLOW.read_text()
    notifier = WORKER_FAILURE_NOTIFIER.read_text()

    assert "verification_rechecks: int = 0" in queue
    assert "excluded_issue_numbers" in queue
    assert "state_priority" in queue
    assert "excluded_issue_numbers=selected_issue_numbers" in worker
    assert "selected_issue_numbers.add(item.issue_number)" in worker
    assert "MAX_VERIFICATION_RECHECKS = 3" in publisher
    assert "send_worker_failure_notification" in worker
    assert "workflow_run:" in workflow
    assert "Drain Splunkbase publish queue" in workflow
    assert "conclusion == 'cancelled'" in workflow
    assert "conclusion == 'timed_out'" in workflow
    assert 'python -m pip install "requests>=2.32.3,<3.0.0"' in workflow
    assert "WORKER_RUN_URL" in notifier
    assert "SLACK_INTERNAL_CHANNEL" in notifier


def test_drain_runs_once_per_hour():
    workflow = DRAIN_WORKFLOW.read_text()

    assert 'cron: "0 * * * *"' in workflow


def test_skipped_direct_publish_cannot_trigger_release_slack():
    workflow = WORKFLOW.read_text()
    notify = workflow.split("\n  notify-slack:\n", 1)[1]

    assert "needs.publish.result != 'skipped'" in notify
    assert "needs.publish.outputs.return_code != ''" in notify


def test_notify_action_uses_the_explicit_connector_workspace():
    action = NOTIFY_ACTION.read_text()
    script = NOTIFY_SCRIPT.read_text()

    assert "CONNECTOR_WORKSPACE: ${{ inputs.workspace_path }}" in action
    assert "GITHUB_WORKSPACE: ${{ inputs.workspace_path }}" not in action
    assert 'os.getenv("CONNECTOR_WORKSPACE"' in script
    assert 'os.getenv("GITHUB_WORKSPACE"' in script


def test_release_version_transition_reaches_both_notification_paths():
    workflow = WORKFLOW.read_text()

    assert (
        "previous_release_version: ${{ steps.publish_action.outputs.previous_release_version }}"
        in workflow
    )
    assert (
        "previous_release_version: ${{ needs.publish.outputs.previous_release_version }}"
        in workflow
    )
    assert (
        '"PREVIOUS_RELEASE_VERSION": outputs["previous_release_version"]'
        in QUEUE_WORKER.read_text()
    )


def test_queue_workflows_execute_self_contained_uv_scripts():
    workflow = DRAIN_WORKFLOW.read_text()
    enqueue_workflow = ENQUEUE_WORKFLOW.read_text()
    enqueue_action = ENQUEUE_ACTION.read_text()
    drain_script = (
        Path(__file__).parent / "scripts" / "drain_splunkbase_publish_queue.py"
    ).read_text()
    worker_script = QUEUE_WORKER.read_text()
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
    assert "uses: actions/setup-python@v5" not in workflow
    assert 'python -m pip install "uv==0.12.0"' in workflow
    assert "uv run .github/scripts/drain_splunkbase_publish_queue_worker.py" in workflow
    assert "uses: astral-sh/setup-uv" not in enqueue_workflow
    assert "uses: actions/setup-python@v5" in enqueue_workflow
    assert 'python -m pip install "uv==0.12.0"' in enqueue_workflow
    assert "uv run .github/scripts/enqueue_splunkbase_publish.py" in enqueue_workflow
    assert "uses: astral-sh/setup-uv" not in enqueue_action
    assert "uses: actions/setup-python@v5" in enqueue_action
    assert 'python -m pip install "uv==0.12.0"' in enqueue_action
    assert 'uv run "${{ github.action_path }}/prepare_enqueue.py"' in enqueue_action
    assert '"backoff>=2.2.1,<3.0.0"' in drain_script
    assert '"requests>=2.32.3,<3.0.0"' in drain_script
    assert '"packaging>=24.2,<26.0"' in drain_script
    assert '"cairosvg==2.7.0"' in worker_script
    assert '"slack-sdk==3.41.0"' in worker_script
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
