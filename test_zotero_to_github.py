#!/usr/bin/env python3
"""
Test script that fetches real publications from Zotero and posts them to GitHub.

Usage:
    # Dry run - just show what would be posted
    python test_zotero_to_github.py --dry-run

    # Actually create issues (will create 5 test issues!)
    python test_zotero_to_github.py

    # Specify number of publications
    python test_zotero_to_github.py --count 3 --dry-run
"""

import argparse
import os
import sys
import time


def load_env_file():
    """Load environment variables from .env file if it exists."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if value and key not in os.environ:
                        os.environ[key] = value


# Load .env file before anything else
load_env_file()

from pyzotero import zotero
from post_to_slack import (
    get_github_project_id,
    create_github_issue,
    add_issue_to_project,
    format_publication_for_github,
    check_issue_exists,
)


def main():
    parser = argparse.ArgumentParser(
        description="Test Zotero to GitHub integration with real data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=5,
        help="Number of recent publications to fetch (default: 5)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be posted, don't create issues"
    )
    parser.add_argument(
        "--state-file",
        default="state.csv",
        help="Path to state.csv to use same subcollections as the bot (default: state.csv)"
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Specific subcollection ID to fetch from (overrides state file)"
    )
    parser.add_argument(
        "--statefile-id",
        default=os.environ.get("STATEFILE_FILE_ID"),
        help="Google Drive file ID to download state.csv (or set STATEFILE_FILE_ID env var)"
    )
    parser.add_argument(
        "--zotero-api-key",
        default=os.environ.get("ZOTERO_API_KEY"),
        help="Zotero API key (or set ZOTERO_API_KEY env var)"
    )
    parser.add_argument(
        "--zotero-library-id",
        default=os.environ.get("ZOTERO_LIBRARY_ID"),
        help="Zotero library ID (or set ZOTERO_LIBRARY_ID env var)"
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_PAT"),
        help="GitHub PAT (or set GITHUB_PAT env var)"
    )
    parser.add_argument(
        "--github-repo",
        default=os.environ.get("GITHUB_TARGET_REPO"),
        help="GitHub repo (or set GITHUB_TARGET_REPO env var)"
    )
    parser.add_argument(
        "--github-project-number",
        default=os.environ.get("GITHUB_PROJECT_NUMBER"),
        help="GitHub project number (or set GITHUB_PROJECT_NUMBER env var)"
    )
    args = parser.parse_args()

    # Validate Zotero inputs
    if not args.zotero_api_key:
        print("ERROR: Zotero API key required. Set ZOTERO_API_KEY in .env")
        print("Get your key at: https://www.zotero.org/settings/keys")
        sys.exit(1)
    if not args.zotero_library_id:
        print("ERROR: Zotero library ID required. Set ZOTERO_LIBRARY_ID in .env")
        print("Find it in your Zotero group URL: zotero.org/groups/LIBRARY_ID/...")
        sys.exit(1)

    # Validate GitHub inputs (only if not dry-run)
    if not args.dry_run:
        if not args.github_token:
            print("ERROR: GitHub token required for live run. Set GITHUB_PAT in .env")
            sys.exit(1)
        if not args.github_repo:
            print("ERROR: GitHub repo required. Set GITHUB_TARGET_REPO in .env")
            sys.exit(1)

    print("=" * 70)
    print("Zotero to GitHub Integration Test")
    print("=" * 70)
    print(f"Zotero Library ID: {args.zotero_library_id}")
    print(f"GitHub Repo: {args.github_repo or 'N/A'}")
    print(f"GitHub Project: #{args.github_project_number or 'N/A'}")
    print(f"Publications to fetch: {args.count}")
    print(f"Dry Run: {args.dry_run}")
    print()

    # Download state file if needed
    if not os.path.exists(args.state_file) and args.statefile_id:
        print("[0/4] Downloading state.csv from Google Drive...")
        try:
            import subprocess
            script_dir = os.path.dirname(os.path.abspath(__file__))
            service_account_file = os.path.join(script_dir, "service_account.json")

            if not os.path.exists(service_account_file):
                print(f"  WARNING: {service_account_file} not found")
                print("  Cannot download state file. Proceeding without subcollections.")
            else:
                result = subprocess.run([
                    sys.executable,
                    os.path.join(script_dir, "download_google_file.py"),
                    "--file_id", args.statefile_id,
                    "--output_path", args.state_file,
                    "--service_account_file", service_account_file
                ], capture_output=True, text=True)

                if result.returncode == 0:
                    print(f"  Downloaded {args.state_file}")
                else:
                    print(f"  WARNING: Failed to download state file: {result.stderr}")
        except Exception as e:
            print(f"  WARNING: Could not download state file: {e}")
        print()

    # Connect to Zotero
    print("[1/4] Connecting to Zotero...")
    try:
        zot = zotero.Zotero(args.zotero_library_id, 'group', args.zotero_api_key)
        print("  Connected!")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # Determine which collection(s) to fetch from
    collection_ids = []
    if args.collection:
        collection_ids = [args.collection]
        print(f"\n[2/4] Fetching from specified collection: {args.collection}")
    elif os.path.exists(args.state_file):
        import pandas as pd
        try:
            state_df = pd.read_csv(args.state_file)
            collection_ids = state_df['subcollectionID'].tolist()
            print(f"\n[2/4] Fetching from {len(collection_ids)} subcollections (from {args.state_file})")
        except Exception as e:
            print(f"\n[2/4] Could not read state file: {e}")
            print("  Falling back to entire library...")
    else:
        print(f"\n[2/4] No state file found at {args.state_file}")
        print("  Fetching from entire library...")

    # Fetch recent publications
    items = []
    try:
        if collection_ids:
            for coll_id in collection_ids:
                coll_items = zot.collection_items_top(coll_id, limit=args.count, sort='dateAdded', direction='desc')
                print(f"  Collection {coll_id}: {len(coll_items)} items")
                items.extend(coll_items)
            # Sort all items by dateAdded and take the most recent
            items.sort(key=lambda x: x.get('data', {}).get('dateAdded', ''), reverse=True)
            items = items[:args.count]
        else:
            items = zot.top(limit=args.count, sort='dateAdded', direction='desc')
        print(f"  Total: {len(items)} publications to process")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    # Get GitHub project ID (if not dry-run)
    project_id = None
    if not args.dry_run and args.github_project_number:
        print(f"\n[3/4] Resolving GitHub project ID...")
        owner = args.github_repo.split('/')[0]
        project_id = get_github_project_id(args.github_token, owner, args.github_project_number)
        if project_id:
            print(f"  Project ID: {project_id}")
        else:
            print("  WARNING: Could not resolve project ID")
    else:
        print(f"\n[3/4] Skipping GitHub project lookup (dry-run or no project)")

    # Process each publication
    print(f"\n[4/4] Processing publications...")
    print("=" * 70)

    for i, pub in enumerate(items, 1):
        data = pub.get('data', {})
        title = data.get('title', 'No title')[:60]

        print(f"\n--- Publication {i}/{len(items)}: {title}...")

        # Format for GitHub
        try:
            issue_title, issue_body = format_publication_for_github(pub, zot)
            print(f"  Title: {issue_title[:70]}{'...' if len(issue_title) > 70 else ''}")
        except Exception as e:
            print(f"  FAILED to format: {e}")
            continue

        if args.dry_run:
            # Check if it would be a duplicate
            existing = check_issue_exists(args.github_token, args.github_repo, issue_title) if args.github_token else None
            if existing:
                print(f"  [DRY-RUN] SKIPPED - Issue already exists")
            else:
                print(f"  [DRY-RUN] Would create issue")
            print("-" * 50)
            print(issue_body)
            print("-" * 50)
        else:
            # Check for duplicate first
            existing_node_id = check_issue_exists(args.github_token, args.github_repo, issue_title)
            if existing_node_id:
                print(f"  SKIPPED - Issue already exists")
                # Still add to project if not there
                if project_id:
                    if add_issue_to_project(args.github_token, project_id, existing_node_id):
                        print(f"  (Added existing issue to project)")
                continue

            # Create issue
            try:
                issue_node_id = create_github_issue(
                    args.github_token,
                    args.github_repo,
                    issue_title,
                    issue_body
                )
                if issue_node_id:
                    print(f"  Created issue!")

                    # Add to project
                    if project_id:
                        if add_issue_to_project(args.github_token, project_id, issue_node_id):
                            print(f"  Added to project!")
                        else:
                            print(f"  WARNING: Could not add to project")
                else:
                    print(f"  FAILED: No issue ID returned")
            except Exception as e:
                print(f"  FAILED: {e}")

            # Rate limiting
            if i < len(items):
                time.sleep(1)

    print("\n" + "=" * 70)
    print("Test complete!")
    if not args.dry_run:
        print(f"Check: https://github.com/{args.github_repo}/issues")
        print("\nNOTE: Remember to delete test issues if needed!")
    print("=" * 70)


if __name__ == "__main__":
    main()
