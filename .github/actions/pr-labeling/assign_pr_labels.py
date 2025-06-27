#!/usr/bin/env python3
"""
Assigns labels to PRs based on properties of the PR
"""

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

# JIRA Configuration
JIRA_URL = "https://splunk.atlassian.net"
JIRA_PROJECT_KEY = "PAPP"
JIRA_LABEL_PATTERN = re.compile(rf"^{JIRA_PROJECT_KEY}-[0-9]+$")
JIRA_SUMMARY_TEMPLATE = "{} - {} App Open Source Submission"
JIRA_DESCRIPTION_TEMPLATE = "Link to PR: {}\n\n{}"
JIRA_ISSUE_TYPE = "Epic"
JIRA_FIELD_EPIC_NAME = "customfield_11501"
JIRA_FIELD_PLATFORM = "customfield_10501"
JIRA_FIELD_PLATFORM_PHANTOM_ID = "23536"
JIRA_FIELD_COLOR = "customfield_12705"
JIRA_FIELD_COLOR_CERTIFIED_ID = "27194"
JIRA_FIELD_COLOR_UNCERTIFIED_ID = "27195"


def create_jira_ticket(jira_user, jira_api_key, app_name, is_certified, pr_info):
    """Create a JIRA ticket for external contributor PRs"""
    if not jira_user or not jira_api_key:
        logging.warning("JIRA credentials not provided, skipping ticket creation")
        return None
    
    ticket_summary = JIRA_SUMMARY_TEMPLATE.format(
        app_name, CERTIFIED_LABEL.capitalize() if is_certified else NOT_CERTIFIED_LABEL.capitalize()
    )

    logging.info("Creating Jira ticket for %s", pr_info.html_url)
    
    # Use basic auth with username and API key
    auth = (jira_user, jira_api_key)
    
    headers = {
        "Content-Type": "application/json"
    }
    
    labels = [f"appname-{app_name}", EXTERNAL_CONTRIBUTOR_LABEL]
    if not is_certified:
        labels.append(NOT_CERTIFIED_LABEL)
    
    payload = {
        "fields": {
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
    }
    
    try:
        response = requests.post(
            f"{JIRA_URL}/rest/api/2/issue",
            headers=headers,
            json=payload,
            auth=auth,
            timeout=30
        )
        response.raise_for_status()
        
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


def get_app_json_from_repo(github_client, repo_name, pr_number):
    """Attempt to read the app JSON from the app's repository"""
    repo = github_client.get_repo(repo_name)
    
    # First try the default branch
    try:
        default_branch = repo.default_branch
        contents = repo.get_contents("", ref=default_branch)
        json_files = [item.name for item in contents if item.name.endswith(".json")]
        app_json_name = find_app_json_name(json_files)
        
        app_json_contents = repo.get_contents(app_json_name, ref=default_branch)
        return json.loads(app_json_contents.decoded_content.decode('utf-8'))
    except Exception:
        logging.info("Could not find app JSON in default branch of %s", repo_name)
    
    try:
        pr = repo.get_pull(pr_number)
        contents = repo.get_contents("", ref=pr.head.sha)
        json_files = [item.name for item in contents if item.name.endswith(".json")]
        app_json_name = find_app_json_name(json_files)
        
        app_json_contents = repo.get_contents(app_json_name, ref=pr.head.sha)
        return json.loads(app_json_contents.decoded_content.decode('utf-8'))
    except Exception:
        logging.info("Could not find app JSON in PR head branch for %s", repo_name)
        return None


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
        app_json = get_app_json_from_repo(github_client, repo_name, pr_number)
        if app_json:
            is_certified = app_json.get("publisher") == "Splunk"
            
            # Add certification label if not already present
            if (CERTIFIED_LABEL not in existing_labels and 
                NOT_CERTIFIED_LABEL not in existing_labels):
                labels_to_add.append(CERTIFIED_LABEL if is_certified else NOT_CERTIFIED_LABEL)
                logging.info(f"Adding label {labels_to_add[-1]} for app {repo_name}")
            
            # Create JIRA ticket for external contributors
            if (is_external_contributor and 
                not any(JIRA_LABEL_PATTERN.match(lb) for lb in existing_labels)):
                
                jira_ticket = create_jira_ticket(
                    jira_user, jira_api_key, 
                    repo_name.split('/')[-1],
                    is_certified, pr
                )
                if jira_ticket:
                    labels_to_add.append(jira_ticket)
                    logging.info("Adding new JIRA ticket label %s", jira_ticket)
    except Exception as e:
        raise RuntimeError(f"Error when processing app JSON: {e}") from e
    
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
