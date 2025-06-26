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


def create_jira_ticket(jira_user, jira_password, app_name, is_certified, pr_info):
    """Create a JIRA ticket for external contributor PRs"""
    if not jira_user or not jira_password:
        logging.warning("JIRA credentials not provided, skipping ticket creation")
        return None
    
    ticket_summary = JIRA_SUMMARY_TEMPLATE.format(
        app_name, CERTIFIED_LABEL.capitalize() if is_certified else NOT_CERTIFIED_LABEL.capitalize()
    )

    logging.info("Creating Jira ticket for %s", pr_info.html_url)
    
    # Use basic auth with username and password
    auth = (jira_user, jira_password)
    
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


def get_app_json_from_repo(github_client, repo_name, pr_number):
    """Attempt to read the app JSON from the app's repository"""
    repo = github_client.get_repo(repo_name)
    
    # First try the default branch
    try:
        default_branch = repo.default_branch
        contents = repo.get_contents("app.json", ref=default_branch)
        return json.loads(contents.decoded_content.decode('utf-8'))
    except Exception:
        logging.info("Could not find app.json in default branch of %s", repo_name)
    
    try:
        pr = repo.get_pull(pr_number)
        contents = repo.get_contents("app.json", ref=pr.head.sha)
        return json.loads(contents.decoded_content.decode('utf-8'))
    except Exception:
        logging.info("Could not find app.json in PR head branch for %s", repo_name)
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
    jira_password = os.getenv('JIRA_PASSWORD')
    repo_name = os.getenv('REPO_NAME')
    pr_number = int(os.getenv('PR_NUMBER'))
    
    if not all([github_token, repo_name, pr_number]):
        logging.error("Missing required environment variables")
        return 1
    
    # Initialize GitHub client
    github_client = Github(github_token)
    repo = github_client.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    # Get user information
    user = pr.user.login
    
    # Check if user is external contributor
    try:
        # Get organization from repo name
        org_name = repo_name.split('/')[0]
        org = github_client.get_organization(org_name)
        is_external_contributor = not org.has_in_members(github_client.get_user(user))
    except Exception:
        # If we can't check membership, assume external
        is_external_contributor = True
        logging.warning("Could not check organization membership for %s", user)
    
    # Check repository permissions
    try:
        collaborator = repo.get_collaborator_permission(user)
        is_partner = collaborator in GITHUB_ROLES_WITH_WRITE_PERMISSION
    except Exception:
        is_partner = False
        logging.warning("Could not check repository permissions for %s", user)
    
    labels_to_add = []
    existing_labels = {label.name for label in pr.labels}
    
    # Add external contributor label if applicable
    if is_external_contributor:
        labels_to_add.append(EXTERNAL_CONTRIBUTOR_LABEL)
        logging.info("Adding label %s for external contributor %s", EXTERNAL_CONTRIBUTOR_LABEL, user)
        
        if not is_partner:
            post_acknowledging_comment(github_client, repo_name, pr_number)
    
    try:
        app_json = get_app_json_from_repo(github_client, repo_name, pr_number)
        if app_json:
            is_certified = app_json.get("publisher") == "Splunk"
            
            # Add certification label if not already present
            if (CERTIFIED_LABEL not in existing_labels and 
                NOT_CERTIFIED_LABEL not in existing_labels):
                labels_to_add.append(CERTIFIED_LABEL if is_certified else NOT_CERTIFIED_LABEL)
                logging.info("Adding label %s for app %s", labels_to_add[-1], repo_name)
            
            # Create JIRA ticket for external contributors
            if (is_external_contributor and 
                not is_partner and 
                not any(JIRA_LABEL_PATTERN.match(lb) for lb in existing_labels)):
                
                jira_ticket = create_jira_ticket(
                    jira_user, jira_password, 
                    repo_name.split('/')[-1],  # Use repo name as app name
                    is_certified, pr
                )
                if jira_ticket:
                    labels_to_add.append(jira_ticket)
                    logging.info("Adding new JIRA ticket label %s", jira_ticket)
    except Exception as e:
        logging.exception("Error processing app.json: %s", e)
    
    # Apply labels
    if labels_to_add:
        current_labels = [label.name for label in pr.labels]
        all_labels = current_labels + labels_to_add
        pr.set_labels(*all_labels)
        logging.info("Added labels: %s", labels_to_add)
    else:
        logging.info("No labels to add!")
    
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    sys.exit(assign_pr_labels())
