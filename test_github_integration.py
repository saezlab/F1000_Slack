#!/usr/bin/env python3
"""
Test script for GitHub Project integration.
Creates a test issue and adds it to a GitHub Project.

Usage:
    python test_github_integration.py \
        --github_token "ghp_xxx" \
        --github_repo "owner/repo" \
        --github_project_number "1"

Or edit .env file and run:
    python test_github_integration.py
"""

import argparse
import os
import sys


def load_env_file():
    """Load environment variables from .env file if it exists."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if value and key not in os.environ:  # Don't override existing env vars
                        os.environ[key] = value


# Load .env file before anything else
load_env_file()

# Import the GitHub functions from the main script
from post_to_slack import (
    get_github_project_id,
    create_github_issue,
    add_issue_to_project,
    format_publication_for_github,
)


def create_mock_publication():
    """Create a mock Zotero publication for testing."""
    return {
        "key": "TEST123",
        "data": {
            "title": "[TEST] GitHub Integration Test - Please Delete",
            "creators": [
                {"firstName": "Test", "lastName": "Author"},
                {"firstName": "Another", "lastName": "Researcher"},
            ],
            "itemType": "journalArticle",
            "journalAbbreviation": "Test Journal",
            "date": "2024-01-15",
            "url": "https://example.com/test-paper",
            "DOI": "10.1234/test.doi",
        },
        "links": {
            "alternate": {
                "href": "https://www.zotero.org/groups/test/items/TEST123"
            }
        },
        "meta": {
            "createdByUser": {
                "username": "test_user"
            }
        }
    }


class MockZotero:
    """Mock Zotero client that returns mock notes."""
    def children(self, key):
        return [
            {
                "data": {
                    "itemType": "note",
                    "note": "<p>This is a <b>sample note</b> that would be attached to the publication. It contains the reviewer's comments or summary.</p><p>Second paragraph with more details.</p>",
                    "dateModified": "2024-01-16T10:30:00Z"
                }
            }
        ]


def main():
    parser = argparse.ArgumentParser(
        description="Test GitHub Project integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--github_token",
        default=os.environ.get("GITHUB_PAT"),
        help="GitHub PAT (or set GITHUB_PAT env var)"
    )
    parser.add_argument(
        "--github_repo",
        default=os.environ.get("GITHUB_TARGET_REPO"),
        help="Target repository owner/repo (or set GITHUB_TARGET_REPO env var)"
    )
    parser.add_argument(
        "--github_project_number",
        default=os.environ.get("GITHUB_PROJECT_NUMBER"),
        help="GitHub Project number (or set GITHUB_PROJECT_NUMBER env var)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only test formatting, don't actually create issues"
    )
    args = parser.parse_args()

    # Validate inputs
    if not args.github_token:
        print("ERROR: GitHub token is required. Use --github_token or set GITHUB_PAT env var")
        sys.exit(1)
    if not args.github_repo:
        print("ERROR: GitHub repo is required. Use --github_repo or set GITHUB_TARGET_REPO env var")
        sys.exit(1)

    print("=" * 60)
    print("GitHub Integration Test")
    print("=" * 60)
    print(f"Repository: {args.github_repo}")
    print(f"Project Number: {args.github_project_number or 'Not specified'}")
    print(f"Dry Run: {args.dry_run}")
    print()

    # Create mock data
    mock_pub = create_mock_publication()
    mock_zot = MockZotero()

    # Test 1: Format publication
    print("[1/4] Testing format_publication_for_github()...")
    try:
        title, body = format_publication_for_github(mock_pub, mock_zot)
        print(f"  Title: {title}")
        print(f"  Body preview: {body[:100]}...")
        print("  OK")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    if args.dry_run:
        print()
        print("Dry run mode - skipping actual API calls")
        print()
        print("Full issue body:")
        print("-" * 40)
        print(body)
        print("-" * 40)
        sys.exit(0)

    # Test 2: Get project ID (if project number provided)
    project_id = None
    if args.github_project_number:
        print()
        print("[2/4] Testing get_github_project_id()...")
        try:
            owner = args.github_repo.split('/')[0]
            project_id = get_github_project_id(args.github_token, owner, args.github_project_number)
            if project_id:
                print(f"  Project ID: {project_id}")
                print("  OK")
            else:
                print("  WARNING: Could not resolve project ID")
                print("  (Issues will be created but not added to project)")
        except Exception as e:
            print(f"  FAILED: {e}")
    else:
        print()
        print("[2/4] Skipping project ID lookup (no project number provided)")

    # Test 3: Create issue
    print()
    print("[3/4] Testing create_github_issue()...")
    try:
        issue_node_id = create_github_issue(
            args.github_token,
            args.github_repo,
            title,
            body
        )
        if issue_node_id:
            print(f"  Issue Node ID: {issue_node_id}")
            print("  OK - Issue created!")
        else:
            print("  FAILED: No issue node ID returned")
            sys.exit(1)
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # Test 4: Add to project
    if project_id and issue_node_id:
        print()
        print("[4/4] Testing add_issue_to_project()...")
        try:
            success = add_issue_to_project(args.github_token, project_id, issue_node_id)
            if success:
                print("  OK - Issue added to project!")
            else:
                print("  FAILED: Could not add issue to project")
        except Exception as e:
            print(f"  FAILED: {e}")
    else:
        print()
        print("[4/4] Skipping add to project (no project ID)")

    print()
    print("=" * 60)
    print("Test complete!")
    print(f"Check your repository: https://github.com/{args.github_repo}/issues")
    if project_id:
        print("Check your project board to verify the issue was added.")
    print()
    print("NOTE: Remember to delete the test issue when done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
