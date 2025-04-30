import json
import argparse
import sys
from pathlib import Path
from typing import Optional
import logging

import backoff

# Add utils to the import path
REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.append(str(REPO_ROOT))

from utils.api.gitlab import GitLabApi


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("app_json_path", help="Path to the JSON file in the Git repository")
    parser.add_argument("old_app_json_path", help="Path to file containing app json before the merge")
    parser.add_argument(
        "-t", "--timeout", type=int, default=1200, help="Max time in seconds to wait for completion"
    )
    parser.add_argument("--publish-code", type=int, help="Status of the publish job")
    return parser.parse_args()


def get_actions_from_json(json_object):
    for action in json_object.get("actions", []):
        yield action["action"]


@backoff.on_predicate(backoff.constant, interval=10, max_time=1200, jitter=None)
def _poll_pipeline_completion(
    gitlab_client: GitLabApi, repo_name: str, pipeline_id: int
) -> Optional[dict]:
    run_details = gitlab_client.get_pipeline_run(repo_name, pipeline_id)
    status = run_details["status"]

    if status in {"created", "pending", "running"}:
        logging.info("Pipeline status %s, retrying...", status)
        return None

    return run_details


def main(args):
    try:
        app_json = Path(args.app_json_path)
        new_json_data = json.loads(app_json.read_text())
        old_app_json = Path(args.old_app_json_path)
        old_json_data = json.loads(old_app_json.read_text())
    except FileNotFoundError:
        logging.error(f"File not found: {app_json}")
        return 1
    except json.JSONDecodeError:
        logging.error("App json could not be parsed")
        return 1

    new_actions = set(get_actions_from_json(new_json_data))
    old_actions = set(get_actions_from_json(old_json_data))
    new_actions_added = new_actions - old_actions
    is_new_app = args.publish_code == 2
    if len(new_actions_added) == 0 and not is_new_app:
        logging.info(
            "No new actions added and app already exists. Not running the metrics pipeline"
        )
        return 0

    gitlab = GitLabApi()
    data = {
        "APP_REPO_NAME": new_json_data["name"],
        "ACTIONS_ADDED": json.dumps(list(new_actions_added)),
        "IS_NEW_APP": is_new_app,
    }
    create_pipeline_resp = gitlab.create_pipeline_run("GitHub Requirements", "main", **data)
    logging.info("Created pipeline run %s", repr(create_pipeline_resp))

    completion_resp = _poll_pipeline_completion(
        gitlab, "GitHub Requirements", create_pipeline_resp["id"]
    )
    logging.info("Pipeline completed with response %s", repr(completion_resp))


if __name__ == "__main__":
    sys.exit(main(parse_args()))
