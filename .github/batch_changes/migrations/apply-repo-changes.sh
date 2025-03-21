#!/bin/bash

set -e  # Exit on error
set -u  # Error on unset

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN environment variable is not set"
  exit 1
fi

ACTION="thaw"
REGEX_PATTERN="^(?!.*(\.github|frictionless_connectors_test)$).*$" # Exclude .github and frictionless_connectors_test
PAGE_COUNT=17  # Each page has 30 repos (needed for 500 repos)

# Parse command args
while [[ $# -gt 0 ]]; do
  case $1 in
    --action)
      ACTION="$2"
      shift 2
      ;;
    --regex)
      REGEX_PATTERN="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--action freeze|thaw|migrate-next-to-main] [--regex PATTERN]"
      exit 1
      ;;
  esac
done

# Validate action
if [[ "$ACTION" != "thaw" && "$ACTION" != "freeze" && "$ACTION" != "migrate-next-to-main" ]]; then
  echo "Action must be one of: 'thaw', 'freeze', or 'migrate-next-to-main'"
  exit 1
fi

# Determine which script to run
if [[ "$ACTION" == "migrate-next-to-main" ]]; then
  SCRIPT_PATH="./migrate-next-to-main.sh"
else
  SCRIPT_PATH="./code-${ACTION}.sh"
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Script not found: $SCRIPT_PATH"
  exit 1
fi

echo "Fetching repositories..."
echo "Using regex filter: $REGEX_PATTERN"
echo "Action: $ACTION"

REPOS=()

for ((page=1; page<=PAGE_COUNT; page++)); do
  echo "Fetching page $page of repositories..."

  RESPONSE=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/orgs/splunk-soar-connectors/repos?per_page=30&page=$page")

  REPO_COUNT=$(echo "$RESPONSE" | grep -c '"full_name":' || true)

  if [[ $REPO_COUNT -eq 0 ]]; then
    echo "No more repositories found on page $page"
    break
  fi

  # Extract repository names and filter by regex
  PAGE_REPOS=$(echo "$RESPONSE" | grep -o '"full_name": *"[^"]*"' | grep -o 'splunk-soar-connectors/[^"]*' | grep -v -E '\.github$|frictionless_connectors_test$' || true)

  if [[ -n "$PAGE_REPOS" ]]; then
    while read -r repo; do
      REPOS+=("$repo")
    done <<< "$PAGE_REPOS"
  fi
done

# Process each matching repository
TOTAL_REPOS=${#REPOS[@]}
echo "Found $TOTAL_REPOS repositories matching pattern '$REGEX_PATTERN'"

if [[ $TOTAL_REPOS -eq 0 ]]; then
  echo "No matching repositories found. Exiting."
  exit 0
fi

echo "Processing repositories..."
for ((i=0; i<TOTAL_REPOS; i++)); do
  REPO="${REPOS[$i]}"
  echo "[$((i+1))/$TOTAL_REPOS] Processing: $REPO"

  bash "$SCRIPT_PATH" "$REPO"

  sleep 1
done

echo "✅ Completed processing $TOTAL_REPOS repositories"
