#!/bin/bash

set -e  # Exit on error
set -u  # Error on unset

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN environment variable is not set"
  exit 1
fi

# Repo to update (run through the apply-repo-changes.sh script for all repos we want)
REPO=${1:-"splunk-soar-connectors/frictionless_connectors_test"}

TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"
echo "Cloning repository..."
git clone "git@github.com:$REPO.git" .

echo "Fetching all branches..."
git fetch --all

remote_branch_exists() {
  git ls-remote --heads origin "$1" | grep -q "$1"
}

# Create "next-archive" from "next"
if remote_branch_exists "next"; then
  echo "Next branch exists, creating next-archive branch..."

  # Get branch protection info from next
  PROTECTION=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/next/protection" || echo "{}")

  # Create and push next-archive branch if not present
  if ! remote_branch_exists "next-archive"; then
    echo "Creating and pushing next-archive branch..."
    git checkout next
    git checkout -b next-archive
    git push origin next-archive
  else
    echo "next-archive branch already exists, skipping creation."
  fi

  # Apply protection to next-archive branch
  echo "Applying protection to next-archive branch..."
  curl -X PUT -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/next-archive/protection" \
    -d '{"required_status_checks":null,"enforce_admins":false, "required_conversation_resolution": true, "required_pull_request_reviews":{"dismiss_stale_reviews":false,"require_code_owner_reviews":false,"required_approving_review_count":1},"restrictions":null}'

  # Remove protection from next branch if it exists
  if [ "$PROTECTION" != "{}" ]; then
    echo "Removing protection from next branch..."
    curl -X DELETE -H "Authorization: token $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      "https://api.github.com/repos/$REPO/branches/next/protection"
  fi
else
  echo "Next branch does not exist, skipping creation of next-archive."
fi

# Set "main" as default branch if it exists
if remote_branch_exists "main"; then
  echo "Setting main as default branch..."
  curl -X PATCH -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO" \
    -d '{"default_branch":"main"}'
else
  echo "Main branch does not exist, cannot set as default branch."
fi

echo "✅ Branch migration complete for $REPO"
echo "Temporary directory: $TEMP_DIR (can delete)"
