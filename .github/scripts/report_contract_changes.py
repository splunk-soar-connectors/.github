"""Maintain one normal PR comment containing the connector contract summary."""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


COMMENT_MARKER = "<!-- soar-contract-summary -->"
BOT = "github-actions[bot]"


def api(method: str, path: str, payload: dict | None = None):
    url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as error:
        message = error.read().decode(errors="replace")
        raise RuntimeError(
            f"GitHub API {method} {path} failed ({error.code}): {message}"
        ) from error


def pages(path: str) -> list[dict]:
    items = []
    page = 1
    while True:
        batch = api("GET", f"{path}?per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def summary(changes: list[dict]) -> str:
    if changes:
        return f"{COMMENT_MARKER}\n## ⚠️ Contract changes\n\n" + "\n\n".join(
            f"### {section['heading']}\n\n" + "\n".join(f"- {item}" for item in section["items"])
            for section in changes
        )
    return f"{COMMENT_MARKER}\n## \u2139\ufe0f No contract changes\n\nNo asset parameter, action input, or action output changes were found."


def is_ours(item: dict) -> bool:
    return item.get("user", {}).get("login") == BOT and (item.get("body") or "").startswith(
        COMMENT_MARKER
    )


def report(repo: str, number: int, head_sha: str, changes: list[dict]) -> None:
    repo_path = "/repos/" + quote(repo, safe="/")
    pr = api("GET", f"{repo_path}/pulls/{number}")
    if pr["head"]["sha"] != head_sha:
        print("PR head advanced while this run was in progress; skipping stale report")
        return

    body = summary(changes)
    comments = [item for item in pages(f"{repo_path}/issues/{number}/comments") if is_ours(item)]
    if len(comments) == 1 and comments[0]["body"] == body:
        print("Contract summary unchanged")
        return
    for comment in comments:
        api("DELETE", f"{repo_path}/issues/comments/{comment['id']}")
    api("POST", f"{repo_path}/issues/{number}/comments", {"body": body})
    print("Contract summary replaced")


def main() -> None:
    changes = json.loads(Path(sys.argv[1]).read_text())["changes"]
    report(
        os.environ["GITHUB_REPOSITORY"],
        int(os.environ["PR_NUMBER"]),
        os.environ["PR_HEAD_SHA"],
        changes,
    )


if __name__ == "__main__":
    main()
