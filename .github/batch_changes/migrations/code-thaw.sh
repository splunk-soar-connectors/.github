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

unfreeze_branch() {
  local branch=$1
  echo "Unfreezing branch: $branch"

  curl -X DELETE -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/$branch/protection"

  # Apply our normal protection rules again
  curl -X PUT -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/$branch/protection" \
    -d '{
      "required_status_checks": null,
      "enforce_admins": false,
      "required_conversation_resolution": true,
      "required_pull_request_reviews": {
        "dismiss_stale_reviews": false,
        "require_code_owner_reviews": false,
        "required_approving_review_count": 1
      },
      "restrictions": null
    }'
}

echo "Checking branches for $REPO..."

TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

if remote_branch_exists "main"; then
  unfreeze_branch "main"
else
  echo "Branch 'main' does not exist in $REPO"
fi

echo "✅ Branch thaw complete for $REPO"
echo "Temporary directory: $TEMP_DIR (can delete)"
