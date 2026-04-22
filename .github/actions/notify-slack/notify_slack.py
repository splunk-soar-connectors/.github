"""
Sends a Slack notification for an app release or pending review.
Adapted from lambdas/src/post_release/slack/notify_slack_channel.py for GitHub Actions.
Channel routing (per support_tag) replaces the Lambda's per-channel deployment model.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

# Nasty hack to make cairo work with pyenv on MacOs
if os.path.exists("/opt/homebrew/lib"):
    from ctypes.macholib import dyld

    dyld.DEFAULT_LIBRARY_FALLBACK.append("/opt/homebrew/lib")

import cairosvg
import requests
from jinja2 import Environment, FileSystemLoader
from slack_sdk import WebClient

TEMPLATES_DIR = Path(__file__).parent / "templates"
RELEASE_TEMPLATE = "release_message.txt.j2"
PENDING_REVIEW_TEMPLATE = "pending_review_message.txt.j2"

SUPPORT_TAG_ALIASES = {
    "splunk": "Splunk-supported",
    "developer": "developer-supported",
    "not_supported": "community-supported",
}

MD_INDENT_MIN_SPACES = 2
INDENTS_TO_SLACK_LIST_SYMBOLS = {0: "●", 1: "⚬", 2: "■", 3: "●", 4: "⚬"}
SLACK_LIST_ITEM_PADDING_LEFT = (
    3  # Number of spaces to indent a list bullet from the beginning of a line
)
SLACK_LIST_ITEM_PADDING_RIGHT = 3  # Number of spaces between a list bullet and the content
SLACK_INDENT_NUM_SPACES = 4
SLACK_LIST_MAX_INDENTS = 5

RELEASE_NOTE_PATTERN = re.compile(r"^\s*\*\s*(?P<note>.+)$")


def _convert_release_notes_to_slack_list(release_notes):
    """
    The Slack API doesn't support list formatting so we'll have to explicitly
    generate the list ahead of time
    """
    if not release_notes:
        return []

    converted_notes, parent_depths = [], [0]
    for note in release_notes:
        if not note:
            continue
        note_match = RELEASE_NOTE_PATTERN.match(note)
        if not note_match:
            logging.warning("Excluding release note in unexpected format: %s", note)
            continue
        curr_depth = note.index("*")
        while True:
            if not parent_depths:
                parent_depths.append(curr_depth)
                break
            diff = curr_depth - parent_depths[-1]
            if 0 <= diff < MD_INDENT_MIN_SPACES:
                parent_depths[-1] = curr_depth
                break
            elif diff > MD_INDENT_MIN_SPACES:
                parent_depths.append(curr_depth)
                break
            else:  # diff < 0
                parent_depths.pop()

        num_indents = min(len(parent_depths) - 1, SLACK_LIST_MAX_INDENTS)
        list_item = (
            [" "] * SLACK_LIST_ITEM_PADDING_LEFT
            + [" "] * num_indents * SLACK_LIST_MAX_INDENTS
            + [INDENTS_TO_SLACK_LIST_SYMBOLS[num_indents]]
            + [" "] * SLACK_LIST_ITEM_PADDING_RIGHT
            + list(note_match.group("note"))
        )
        converted_notes.append("".join(list_item))

    return converted_notes


def _build_message(
    app_name,
    support_tag,
    splunk_base_url,
    release_notes=None,
    new_app=False,
    template_name=RELEASE_TEMPLATE,
):
    support_alias = SUPPORT_TAG_ALIASES.get(support_tag)
    if not support_alias:
        err_msg = f"Unrecognized support tag {support_tag}"
        logging.error(err_msg)
        raise ValueError(err_msg)

    jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = jinja_env.get_template(template_name)
    return template.render(
        app_name=app_name,
        support_alias=support_alias,
        splunk_base_url=splunk_base_url,
        release_notes=_convert_release_notes_to_slack_list(release_notes),
        new_app=new_app,
    )


def _convert_svg_logo_to_png(repo_name, repo_svg_logo_path):
    """
    Reads the SVG logo from the local checkout (GITHUB_WORKSPACE) and returns PNG bytes.
    If the SVG embeds a raster image (data:image/png;base64,...), extract it directly
    rather than using cairosvg, which doesn't handle embedded bitmaps.
    """
    import base64
    import re as _re

    workspace = os.getenv("GITHUB_WORKSPACE", ".")
    svg_path = Path(workspace) / repo_svg_logo_path
    logging.info("Reading SVG logo from %s (repo: %s)", svg_path, repo_name)
    svg_bytes = svg_path.read_bytes()

    match = _re.search(rb'href=["\']data:image/png;base64,([A-Za-z0-9+/=\s]+)["\']', svg_bytes)
    if match:
        logging.info("Extracting embedded PNG from SVG")
        return base64.b64decode(match.group(1).replace(b"\n", b"").replace(b" ", b""))

    return cairosvg.svg2png(bytestring=svg_bytes)


def _notify_slack_channel(slack_client, slack_channel, release_data):
    if release_data["app_logo"].split(".")[-1] != "svg":
        err_msg = f"Expected logo to be in SVG but got {release_data['app_logo']}"
        logging.error(err_msg)
        raise ValueError(err_msg)

    template_name = PENDING_REVIEW_TEMPLATE if release_data["new_app"] else RELEASE_TEMPLATE
    release_message = _build_message(
        app_name=release_data["app_name"],
        support_tag=release_data["support_tag"],
        release_notes=release_data["release_notes"],
        splunk_base_url=release_data["splunk_base_url"],
        new_app=release_data["new_app"],
        template_name=template_name,
    )
    png_bytes = _convert_svg_logo_to_png(
        repo_name=release_data["repo_name"], repo_svg_logo_path=release_data["app_logo"]
    )

    logging.info("Uploading release message to %s", slack_channel)

    # Step 1: Get upload URL from Slack
    filename = f"{release_data['repo_name']}_logo.png"
    file_size = len(png_bytes)

    logging.info("Getting upload URL for file %s (size: %d bytes)", filename, file_size)
    upload_url_response = slack_client.files_getUploadURLExternal(
        filename=filename, length=file_size
    )

    if not upload_url_response.get("ok"):
        raise RuntimeError(f"Failed to get upload URL: {upload_url_response.get('error')}")

    upload_url = upload_url_response["upload_url"]
    file_id = upload_url_response["file_id"]

    # Step 2: Upload file to the provided URL
    logging.info("Uploading file to Slack (file_id: %s)", file_id)
    upload_response = requests.post(upload_url, data=png_bytes)
    upload_response.raise_for_status()

    # Step 3: Complete the upload with channel and message
    logging.info("Completing upload to channel %s", slack_channel)
    complete_response = slack_client.files_completeUploadExternal(
        files=[{"id": file_id, "title": filename}],
        channel_id=slack_channel,
        initial_comment=release_message,
    )

    if not complete_response.get("ok"):
        raise RuntimeError(f"Failed to complete upload: {complete_response.get('error')}")

    logging.info("Successfully uploaded file to Slack")


def main():
    logging.getLogger().setLevel(logging.INFO)

    release_data = {
        "app_name": os.environ["APP_NAME"],
        "app_logo": os.environ["APP_LOGO"],
        "repo_name": os.environ["REPO_NAME"],
        "release_version": os.environ["RELEASE_VERSION"],
        "release_notes": json.loads(os.environ["RELEASE_NOTES"]),
        "new_app": os.environ["NEW_APP"].lower() == "true",
        "support_tag": os.environ["SUPPORT_TAG"],
        "splunk_base_url": os.environ["SPLUNK_BASE_URL"],
    }

    # Route: splunk-supported → internal + community channels, everything else → community only
    if release_data["support_tag"] == "splunk":
        _notify_slack_channel(
            WebClient(token=os.environ["SLACK_INTERNAL_TOKEN"]),
            os.environ["SLACK_INTERNAL_CHANNEL"],
            release_data,
        )

    _notify_slack_channel(
        WebClient(token=os.environ["SLACK_COMMUNITY_TOKEN"]),
        os.environ["SLACK_COMMUNITY_CHANNEL"],
        release_data,
    )


if __name__ == "__main__":
    sys.exit(main())
