#!/bin/bash

set -e  # Exit on error
set -u  # Error on unset

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN environment variable is not set"
  exit 1
fi

# Repos to update (TODO: change to all when running)
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

# 1: Rename "main" to "main-archive" if it exists
if remote_branch_exists "main"; then
  echo "Main branch exists, renaming to main-archive..."

  # Get branch protection info
  PROTECTION=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/main/protection" || echo "{}")

  # Remove branch protection if it exists
  if [ "$PROTECTION" != "{}" ]; then
    echo "Removing protection from main branch..."
    curl -X DELETE -H "Authorization: token $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      "https://api.github.com/repos/$REPO/branches/main/protection"
  fi

  # Create and push main-archive branch if not present
  if ! git show-ref --verify --quiet refs/heads/main-archive; then
    echo "Creating and pushing main-archive branch..."
    git checkout main
    git checkout -b main-archive
    git push origin main-archive
  else
    echo "main-archive branch already exists, skipping creation."
  fi

  # Temp change default branch to main-archive before deleting main
  echo "Setting main-archive as temporary default branch..."
  curl -X PATCH -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO" \
    -d '{"default_branch":"main-archive"}'

  # Delete main branch
  echo "Deleting main branch..."
  git push origin --delete main

  # Apply protection to main-archive branch (keep it protected)
  if [ "$PROTECTION" != "{}" ]; then
    echo "Reapplying protection to main-archive branch..."
    curl -X PUT -H "Authorization: token $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      "https://api.github.com/repos/$REPO/branches/main-archive/protection" \
      -d '{"required_status_checks":null,"enforce_admins":true,"required_pull_request_reviews":{"dismissal_restrictions":{},"dismiss_stale_reviews":false,"require_code_owner_reviews":false,"required_approving_review_count":1, "require_conversation_resolution": true},"restrictions":null}'
  fi
else
  echo "Main branch does not exist, skipping rename."
fi

# 2: Create "main" from "next" and apply protections
if remote_branch_exists "next"; then
  echo "Next branch exists, creating main from next..."

  # Create and push main from next
  git checkout -b next origin/next
  git checkout -b main
  git push origin main

  # Apply standard protection to main branch
  echo "Applying protection to main branch..."
  curl -X PUT -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO/branches/main/protection" \
    -d '{"required_status_checks":null,"enforce_admins":true,"required_pull_request_reviews":{"dismissal_restrictions":{},"dismiss_stale_reviews":false,"require_code_owner_reviews":false,"required_approving_review_count":1, "require_conversation_resolution": true},"restrictions":null}'
  
  # Set "main" as default branch
  echo "Setting main as default branch..."
  curl -X PATCH -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/$REPO" \
    -d '{"default_branch":"main"}'
else
  echo "Next branch does not exist, skipping creation of main from next."
fi

echo "✅ Branch migration complete for $REPO"
echo "Temporary directory: $TEMP_DIR (can delete)"
