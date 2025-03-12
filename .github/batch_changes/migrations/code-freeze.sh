#!/bin/bash

set -e  # Exit on error
set -u  # Error on unset

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN environment variable is not set"
  exit 1
fi

# Repo to update (run through the apply-repo-changes.sh script for all repos we want)
REPO=${1:-"splunk-soar-connectors/frictionless_connectors_test"}

remote_branch_exists() {
  git ls-remote --heads "https://github.com/${REPO}.git" "$1" | grep -q "$1"
}

freeze_branch() {
  local branch=$1
  echo "Freezing branch: $branch"
  
  # Strict freeze protection rules
  curl -X PUT -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/$branch/protection" \
    -d '{
      "required_status_checks": {"strict": true, "contexts": ["freeze-check"]},
      "enforce_admins": true,
      "required_pull_request_reviews": {
        "dismiss_stale_reviews": true,
        "require_code_owner_reviews": true,
        "required_approving_review_count": 6
      },
      "restrictions": {
        "users": [],
        "teams": []
      }
    }'
}

echo "Checking branches for $REPO..."

TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

# Check if branches exist and freeze them
if remote_branch_exists "next"; then
  freeze_branch "next"
else
  echo "Branch 'next' does not exist in $REPO"
fi

if remote_branch_exists "main"; then
  freeze_branch "main"
else
  echo "Branch 'main' does not exist in $REPO"
fi

echo "✅ Branch freeze complete for $REPO"
echo "Temporary directory: $TEMP_DIR (can delete)"
