#!/usr/bin/env python3
"""
Assigns labels to PRs based on properties of the PR
"""

import ast
import json
import logging
import os
import re
import sys

import requests
from github import Github

GITHUB_ROLES_WITH_WRITE_PERMISSION = {"write", "maintain", "admin"}

ACK_COMMENT_OTHER_PRS_OPEN = (
    "Thank you for your submission! We have a total of {} PRs open right now, "
    "and we are working hard on all of them! We will take a look as soon as we can."
)
ACK_COMMENT_NO_PRS_OPEN = "Thank you for your submission! We will take a look as soon as we can."

EXTERNAL_CONTRIBUTOR_LABEL = "external-contributor"
CERTIFIED_LABEL = "splunk-supported"
NOT_CERTIFIED_LABEL = "developer-supported"

# An app is "Splunk supported" only when its manifest declares this publisher.
CERTIFIED_PUBLISHER = "Splunk"
# Authored entry point for SDK-based apps (the manifest is generated at build
# time, so publisher/appid/name live in the ``App(...)`` call in this file).
SDK_APP_ENTRY = "src/app.py"

# JIRA Configuration
JIRA_URL = "https://splunk.atlassian.net"
JIRA_PROJECT_KEY = "PAPP"
JIRA_LABEL_PATTERN = re.compile(rf"^{JIRA_PROJECT_KEY}-[0-9]+$")
JIRA_SUMMARY_TEMPLATE = "{} - {} App Open Source Submission"
NEW_APP_SUMMARY_PREFIX = "[New App] "
JIRA_DESCRIPTION_TEMPLATE = "Link to PR: {}\n\n{}"
JIRA_ISSUE_TYPE = "Epic"
JIRA_FIELD_EPIC_NAME = "customfield_11501"
JIRA_FIELD_PLATFORM = "customfield_10501"
JIRA_FIELD_PLATFORM_PHANTOM_ID = "23536"
JIRA_FIELD_COLOR = "customfield_12705"
JIRA_FIELD_COLOR_CERTIFIED_ID = "27194"
JIRA_FIELD_COLOR_UNCERTIFIED_ID = "27195"


def get_jira_account_id(auth):
    """Return the accountId of the authenticated Jira user, or None.

    Needed because ``reporter`` is a required field on the PAPP create screen.
    """
    try:
        response = requests.get(
            f"{JIRA_URL}/rest/api/2/myself",
            headers={"Accept": "application/json"},
            auth=auth,
            timeout=30,
        )
        if response.status_code >= 400:
            logging.error(
                "Could not resolve Jira accountId (HTTP %s): %s",
                response.status_code,
                response.text,
            )
            return None
        return response.json().get("accountId")
    except requests.exceptions.RequestException as ex:
        logging.exception("Failed to resolve Jira accountId: %s", ex)
        return None


def create_jira_ticket(jira_user, jira_api_key, app_name, is_certified, pr_info, is_new_app=False):
    """Create a JIRA ticket for external contributor PRs"""
    if not jira_user or not jira_api_key:
        logging.warning("JIRA credentials not provided, skipping ticket creation")
        return None
    
    ticket_summary = JIRA_SUMMARY_TEMPLATE.format(
        app_name, CERTIFIED_LABEL.capitalize() if is_certified else NOT_CERTIFIED_LABEL.capitalize()
    )
    if is_new_app:
        ticket_summary = f"{NEW_APP_SUMMARY_PREFIX}{ticket_summary}"

    logging.info("Creating Jira ticket for %s", pr_info.html_url)
    
    # Use basic auth with username and API key
    auth = (jira_user, jira_api_key)
    
    headers = {
        "Content-Type": "application/json"
    }
    
    labels = [f"appname-{app_name}", EXTERNAL_CONTRIBUTOR_LABEL]
    if not is_certified:
        labels.append(NOT_CERTIFIED_LABEL)

    fields = {
        "project": {"key": JIRA_PROJECT_KEY},
        "summary": ticket_summary,
        "description": JIRA_DESCRIPTION_TEMPLATE.format(pr_info.html_url, pr_info.body or ""),
        "issuetype": {"name": JIRA_ISSUE_TYPE},
        JIRA_FIELD_EPIC_NAME: ticket_summary,
        JIRA_FIELD_PLATFORM: {"id": JIRA_FIELD_PLATFORM_PHANTOM_ID},
        JIRA_FIELD_COLOR: {
            "id": JIRA_FIELD_COLOR_CERTIFIED_ID
            if is_certified
            else JIRA_FIELD_COLOR_UNCERTIFIED_ID
        },
        "labels": labels,
    }

    # `reporter` is required on the PAPP create screen. Interactive users get it
    # auto-populated, but the CI service account does not, so the create fails
    # with a 400. Set it explicitly to the authenticated account.
    reporter_account_id = get_jira_account_id(auth)
    if reporter_account_id:
        fields["reporter"] = {"accountId": reporter_account_id}
    else:
        logging.warning(
            "Proceeding without an explicit reporter; the create may fail if "
            "the account cannot default the required reporter field."
        )

    try:
        response = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            headers=headers,
            json={"fields": fields},
            auth=auth,
            timeout=30,
        )
        if response.status_code >= 400:
            # Surface the actual field-level validation error so failures are
            # debuggable instead of a bare "400 Bad Request".
            logging.error(
                "Jira rejected ticket creation (HTTP %s): %s",
                response.status_code,
                response.text,
            )
            return None

        result = response.json()
        logging.info("Response from Jira: %s", result)
        return result["key"]
    except requests.exceptions.RequestException as ex:
        logging.exception("Failed to create JIRA ticket: %s", ex)
        return None


def check_if_internal_contributor(github_client, repo_name, user):
    """Check if user is internal by checking repository collaborator status"""
    try:
        repo = github_client.get_repo(repo_name)
        
        # Check if user is a collaborator on the repository
        try:
            permission = repo.get_collaborator_permission(user)
            logging.info("User %s has '%s' permission on repository %s", user, permission, repo_name)
            return permission in GITHUB_ROLES_WITH_WRITE_PERMISSION
        except Exception:
            logging.info("User %s is not a direct collaborator on repository %s", user, repo_name)
            return False
            
    except Exception as e:
        logging.warning("Could not check collaborator status: %s", str(e))
        return False


def find_app_json_name(json_filenames):
    """Return most likely app json"""
    filtered_json_filenames = []
    for fname in json_filenames:

        # Ignore the postman collection JSON files
        if "postman_collection" in fname.lower():
            continue
        filtered_json_filenames.append(fname)

    if len(filtered_json_filenames) == 0:
        raise ValueError("No JSON file found in top level of app repo! Aborting tests...")

    if len(filtered_json_filenames) > 1:
        raise ValueError(
            f"Multiple JSON files found in top level of app repo: {filtered_json_filenames}."
            "Aborting because there should be exactly one top level JSON file."
        )

    # Only one json file in the top level, must be the app's json
    return filtered_json_filenames[0]


def _read_traditional_manifest_at_ref(repo, ref):
    """Read the top-level JSON manifest for a traditional app, or None.

    Traditional apps commit a single top-level ``*.json`` manifest carrying
    ``publisher``/``appid``/``name``.
    """
    try:
        contents = repo.get_contents("", ref=ref)
    except Exception:
        return None
    json_files = [item.name for item in contents if item.name.endswith(".json")]
    try:
        app_json_name = find_app_json_name(json_files)
    except ValueError:
        return None
    try:
        raw = repo.get_contents(app_json_name, ref=ref).decoded_content.decode("utf-8")
        manifest = json.loads(raw)
    except Exception:
        return None
    return {
        "publisher": manifest.get("publisher"),
        "appid": manifest.get("appid"),
        "name": manifest.get("name"),
        "format": "traditional",
    }


def _module_string_constants(tree):
    """Map top-level ``NAME = "literal"`` assignments to their string values.

    Lets us resolve kwargs that reference module constants (e.g. real apps
    write ``appid=APP_ID`` / ``name=APP_NAME``) rather than inline literals.
    """
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
    return constants


def _find_app_call(tree):
    """Return the ``ast.Call`` node for ``App(...)``, or None.

    Matches the constructor by callee name, so chained calls like
    ``App(...).enable_webhooks(...)`` and unrelated ``name=`` kwargs on action
    decorators are handled correctly by AST structure (not text proximity).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "App":
            return node
    return None


def _app_kwarg(call, key, constants):
    """Resolve a string kwarg from the ``App(...)`` call, or None.

    Accepts inline string literals and references to module-level string
    constants; anything else (computed expressions) is left as None.
    """
    for keyword in call.keywords:
        if keyword.arg != key:
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name):
            return constants.get(value.id)
        return None
    return None


def _read_sdk_manifest_at_ref(repo, ref):
    """Read SDK app metadata from ``src/app.py``'s ``App(...)`` call, or None.

    SDK apps do not commit a manifest; ``publisher``/``appid``/``name`` are
    declared as keyword arguments to ``App(...)`` in the authored entry point.
    Parsed via AST so nested parens, multi-line calls, and constant references
    are handled robustly.
    """
    try:
        raw = repo.get_contents(SDK_APP_ENTRY, ref=ref).decoded_content.decode("utf-8")
    except Exception:
        return None
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return None
    call = _find_app_call(tree)
    if call is None:
        return None
    constants = _module_string_constants(tree)
    return {
        "publisher": _app_kwarg(call, "publisher", constants),
        "appid": _app_kwarg(call, "appid", constants),
        "name": _app_kwarg(call, "name", constants),
        "format": "sdk",
    }


def get_app_metadata_at_ref(repo, ref):
    """Return ``{publisher, appid, name, format}`` for the app at ``ref``, or None.

    Format-agnostic: tries the traditional JSON manifest first, then the SDK
    ``src/app.py`` entry point. Returns None when neither marker exists, which
    means the ref does not contain an app (e.g. a docs-only PR).
    """
    return _read_traditional_manifest_at_ref(repo, ref) or _read_sdk_manifest_at_ref(repo, ref)


def get_app_context(github_client, repo_name, pr_number):
    """Resolve app metadata and whether the PR introduces a brand-new app.

    Returns ``(metadata, is_new_app)``. ``is_new_app`` is True when no app
    exists on the default branch but one is present in the PR head. For
    existing apps the default-branch metadata is authoritative (so the PR
    cannot flip support status by editing the publisher field).
    """
    repo = github_client.get_repo(repo_name)

    base_meta = get_app_metadata_at_ref(repo, repo.default_branch)
    if base_meta is not None:
        return base_meta, False

    # Nothing on the default branch -> a PR that adds an app is a new app.
    try:
        pr = repo.get_pull(pr_number)
        head_meta = get_app_metadata_at_ref(repo, pr.head.sha)
    except Exception:
        logging.info("Could not read app metadata from PR head for %s", repo_name)
        head_meta = None
    return head_meta, head_meta is not None


def splunkbase_is_supported(appid):
    """Return True/False if Splunkbase marks the app Splunk-supported, else None.

    Used for existing apps per the agreed model. Returns None (undecided) when
    Splunkbase credentials or the helper are unavailable, or the app/field is
    missing, so callers can fall back gracefully.
    """
    user = os.getenv("SPLUNKBASE_USER")
    password = os.getenv("SPLUNKBASE_PASSWORD")
    if not (user and password and appid):
        logging.warning(
            "Splunkbase support lookup skipped (missing credentials or appid); "
            "support status left undecided."
        )
        return None

    try:
        # Reuse the shared CI helper. Imported lazily so a missing dependency
        # (e.g. backoff) degrades gracefully instead of failing the action.
        utils_api = os.path.join(
            os.path.dirname(__file__), "..", "..", "utils", "api"
        )
        if utils_api not in sys.path:
            sys.path.insert(0, utils_api)
        from splunkbase import Splunkbase  # type: ignore

        results = Splunkbase(user, password).get_apps(extra_params={"appid": appid})
    except Exception as ex:
        logging.warning("Splunkbase support lookup failed: %s", ex)
        return None

    if not results:
        logging.info("No Splunkbase entry for appid %s", appid)
        return None
    support = (results[0].get("support") or "").lower()
    logging.info("Splunkbase support level for %s: %r", appid, support)
    return "splunk" in support


def determine_is_certified(metadata, is_new_app):
    """Apply the agreed support model and return the certification flag.

    New app  -> publisher field decides (Splunk => supported).
    Existing -> Splunkbase ``support`` field decides; falls back to the
                publisher field only when Splunkbase is undecided.
    """
    publisher_supported = metadata.get("publisher") == CERTIFIED_PUBLISHER

    if is_new_app:
        return publisher_supported

    sb_supported = splunkbase_is_supported(metadata.get("appid"))
    if sb_supported is None:
        logging.info("Falling back to publisher field for support decision.")
        return publisher_supported
    return sb_supported


def post_acknowledging_comment(github_client, repo_name, pr_number):
    """Post an acknowledging comment for external contributors"""
    repo = github_client.get_repo(repo_name)
    
    # Search for open PRs with external-contributor label
    query = f"is:pr is:open repo:{repo_name} label:{EXTERNAL_CONTRIBUTOR_LABEL}"
    open_prs = list(github_client.search_issues(query))
    
    if open_prs:
        comment = ACK_COMMENT_OTHER_PRS_OPEN.format(len(open_prs))
    else:
        comment = ACK_COMMENT_NO_PRS_OPEN
    
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(comment)


def assign_pr_labels():
    """Main function to assign PR labels"""
    github_token = os.getenv('GITHUB_TOKEN')
    jira_user = os.getenv('JIRA_USER')
    jira_api_key = os.getenv('JIRA_API_KEY')
    repo_name = os.getenv('REPO_NAME')
    pr_number = int(os.getenv('PR_NUMBER'))
    
    if not all([github_token, repo_name, pr_number]):
        raise ValueError("Missing required environment variables: GITHUB_TOKEN, REPO_NAME, or PR_NUMBER")
    
    github_client = Github(github_token)
    repo = github_client.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    user = pr.user.login
    
    # Check if user is external contributor
    is_internal = check_if_internal_contributor(github_client, repo_name, user)
    is_external_contributor = not is_internal
    
    logging.info("User %s is %s contributor", 
                user, "external" if is_external_contributor else "internal")
    
    
    labels_to_add = []
    existing_labels = {label.name for label in pr.labels}
    
    if is_external_contributor:
        labels_to_add.append(EXTERNAL_CONTRIBUTOR_LABEL)
        logging.info("Adding label %s for external contributor %s", EXTERNAL_CONTRIBUTOR_LABEL, user)
        
        # All external contributors get an acknowledgment comment
        post_acknowledging_comment(github_client, repo_name, pr_number)
    
    try:
        metadata, is_new_app = get_app_context(github_client, repo_name, pr_number)

        if metadata is None:
            logging.info(
                "No app marker found for %s (not an app PR); skipping support "
                "label and Jira creation.",
                repo_name,
            )
        else:
            is_certified = determine_is_certified(metadata, is_new_app)
            logging.info(
                "App %s: new_app=%s, format=%s, certified=%s",
                repo_name, is_new_app, metadata.get("format"), is_certified,
            )

            # Add certification label if not already present
            if (CERTIFIED_LABEL not in existing_labels and
                    NOT_CERTIFIED_LABEL not in existing_labels):
                labels_to_add.append(CERTIFIED_LABEL if is_certified else NOT_CERTIFIED_LABEL)
                logging.info("Adding label %s for app %s", labels_to_add[-1], repo_name)

            # Create JIRA ticket for external contributors
            if (is_external_contributor and
                    not any(JIRA_LABEL_PATTERN.match(lb) for lb in existing_labels)):

                jira_ticket = create_jira_ticket(
                    jira_user, jira_api_key,
                    repo_name.split('/')[-1],
                    is_certified, pr,
                    is_new_app=is_new_app,
                )
                if jira_ticket:
                    labels_to_add.append(jira_ticket)
                    logging.info("Adding new JIRA ticket label %s", jira_ticket)
    except Exception as e:
        raise RuntimeError(f"Error when processing app metadata: {e}") from e
    
    # Apply labels
    if labels_to_add:
        current_labels = [label.name for label in pr.labels]
        all_labels = current_labels + labels_to_add
        pr.set_labels(*all_labels)
        logging.info("Added labels: %s", labels_to_add)
    else:
        logging.info("No labels to add!")


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    try:
        assign_pr_labels()
    except Exception:
        logging.exception("PR label application failed")
        sys.exit(1)
