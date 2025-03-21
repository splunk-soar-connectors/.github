#!/bin/bash

# Script to list all non-archived repositories with releases in a GitHub organization for using in sourcegraph
# Put repos in sourcegraph like
# - repository: github.com/splunk-soar-connectors/cydarm
# - repository: github.com/splunk-soar-connectors/ciscosma
# - repository: etc...

# Usage: ./get-batches.sh

set -e

PAGE_COUNT=18  # Each page has 30 repos (up to ~500 repos)

# Ensure GITHUB_TOKEN is set
if [ -z "$GITHUB_TOKEN" ]; then
  echo "ERROR: GITHUB_TOKEN environment variable must be set"
  exit 1
fi

TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

ELIGIBLE_REPOS_FILE="$TEMP_DIR/eligible_repos.txt"
touch "$ELIGIBLE_REPOS_FILE"

echo "Fetching repositories for organization: splunk-soar-connectors"

for ((page=1; page<=PAGE_COUNT; page++)); do
  echo "Fetching page $page of repositories..."
  
  repos=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/orgs/splunk-soar-connectors/repos?per_page=30&page=$page")
  
  # Check if we got empty results (end of pagination)
  repo_count=$(echo "$repos" | grep -o '"full_name":' | wc -l)
  if [ "$repo_count" -eq 0 ]; then
    echo "No more repositories found on page $page"
    break
  fi
  
  # Process each repository
  for repo in $(echo "$repos" | grep -o '"full_name": *"[^"]*"' | grep -o "splunk-soar-connectors/[^\"]*"); do
    echo "Checking repository: $repo"
    
    # Check if repository is archived or has no releases
    is_archived=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      "https://api.github.com/repos/$repo" | \
      grep -o '"archived":\s*\(true\|false\)' | cut -d: -f2 | tr -d ' ')
    
    if [ "$is_archived" == "true" ]; then
      echo "  Repository is archived, skipping"
      continue
    fi
    
    releases=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github.v3+json" \
      "https://api.github.com/repos/$repo/releases?per_page=1")
    
    release_count=$(echo "$releases" | grep -o '"id":' | wc -l)
    
    if [ "$release_count" -gt 0 ]; then
      echo "  Repository has releases, adding to list"
      echo "$repo" >> "$ELIGIBLE_REPOS_FILE"
    else
      echo "  Repository has no releases, skipping"
    fi
  done
  
  sleep 1
done

# Sort
sort "$ELIGIBLE_REPOS_FILE" -o "$ELIGIBLE_REPOS_FILE"

total_repos=$(wc -l < "$ELIGIBLE_REPOS_FILE")

echo ""
echo "Found $total_repos eligible repositories"
echo ""

while read -r repo; do
  echo "- repository: github.com/$repo"
done < "$ELIGIBLE_REPOS_FILE"

echo ""
echo "Complete! Listed $total_repos repositories."
