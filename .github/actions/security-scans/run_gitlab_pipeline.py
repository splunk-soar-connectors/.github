"""
Creates a run for a specified pipeline in GitLab and polls for its completion
"""

import argparse
import json
import logging
import sys
import os

import backoff

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from utils.api.gitlab import GitLabApi


def create_arg_parser():
    help_str = " ".join(line.strip() for line in __doc__.splitlines())
    argparser = argparse.ArgumentParser(description=help_str)
    argparser.add_argument(
        "repo_name", type=str, help="Name of the GitLab repo to create a pipeline run for"
    )
    argparser.add_argument("repo_branch", type=str, help="Branch of the repo to use")
    argparser.add_argument(
        "-v",
        "--pipeline-vars",
        type=str,
        help="""JSON formatted string of variables to pass to the pipeline,
                                   eg, \'{"key1": "val1", "key2", "val2"}\'""",
    )
    argparser.add_argument(
        "-t", "--timeout", type=int, default=1200, help="Max time in seconds to wait for completion"
    )
    argparser.add_argument(
        "-o", "--out", type=str, default="pipeline_run.json", help="File to write run details"
    )
    return argparser


@backoff.on_predicate(backoff.constant, interval=10, max_time=1200, jitter=None)
def _poll_pipeline_completion(gitlab_client, repo_name, pipeline_id):
    run_details = gitlab_client.get_pipeline_run(repo_name, pipeline_id)
    status = run_details["status"]

    if status in {"created", "pending", "running"}:
        logging.info("Pipeline status %s, retrying...", status)
        return None

    return run_details


def main(**kwargs):
    gitlab = GitLabApi()
    create_pipeline_resp = gitlab.create_pipeline_run(
        kwargs["repo_name"], kwargs["repo_branch"], **json.loads(kwargs["pipeline_vars"])
    )
    logging.info("Created pipeline run %s", repr(create_pipeline_resp))

    completion_resp = _poll_pipeline_completion(
        gitlab, kwargs["repo_name"], create_pipeline_resp["id"]
    )
    logging.info("Pipeline completed with response %s", repr(completion_resp))

    with open(kwargs["out"], "w") as out:
        json.dump(completion_resp, out)
        logging.info("Results written to %s", out.name)

    exit(0 if completion_resp["status"] == "success" else 1)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    parser = create_arg_parser()
    options = vars(parser.parse_args())
    main(**options)
