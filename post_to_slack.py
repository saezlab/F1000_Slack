#!/usr/bin/env python3
import argparse
import pandas as pd
import time
import logging
import re
import sys  # Needed to exit on error
from fuzzywuzzy import fuzz # match slack names and zotero mentiones
from datetime import datetime, timezone
from pyzotero import zotero
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request
import urllib.error
import json

# ------------------------------------------------------------------------------
# Setup a simple logfile (overwriting any previous log on each run)
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    filemode='w',  # Overwrites previous log on each run
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ------------------------------------------------------------------------------
def validate_inputs(file_path, zotero_api_key, zotero_library_id, slack_token):
    """Validate required inputs."""
    if not file_path:
        raise ValueError("State file path is required.")
    if not zotero_api_key:
        raise ValueError("Zotero API key is required.")
    if not zotero_library_id:
        raise ValueError("Zotero library ID is required.")
    if not slack_token:
        raise ValueError("Slack Bot API token is required.")

# ------------------------------------------------------------------------------
def parse_last_date(last_date):
    """
    Parse an ISO‑formatted last_date string.
    Expected format: "YYYY-MM-DDTHH:MM:SSZ" (or with an offset)
    """
    try:
        if last_date.endswith("Z"):
            last_date = last_date.replace("Z", "+00:00")
        return datetime.fromisoformat(last_date)
    except Exception as e:
        logging.error(f"Error parsing state file date '{last_date}': {e}")
        raise ValueError(f"Invalid ISO date format for lastDate: {last_date}")

# ------------------------------------------------------------------------------
def fetch_new_publications(zot, collection_id, last_date):
    """
    Fetch new or modified publications from a specific Zotero subcollection.
    Returns items that either:
    - Have a publication date (dateModified or dateAdded) later than the state file's last_date
    - Have notes that were added or modified after the last_date
    """
    new_items = []
    try:
        last_date_dt = parse_last_date(last_date)
    except Exception as e:
        logging.error(f"Error parsing last_date: {e}")
        raise  # abort if last_date cannot be parsed

    try:
        items = zot.collection_items_top(collection_id, limit=100, sort='dateAdded', direction='desc')
    except Exception as e:
        logging.error(f"Error fetching items from collection '{collection_id}': {e}")
        raise

    for item in items:
        data = item.get('data', {})
        is_new = False
        
        # Check paper's dates
        date_str = data.get('dateModified') or data.get('dateAdded')
        if date_str:
            try:
                pub_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                if pub_date > last_date_dt:
                    is_new = True
            except Exception as e:
                logging.error(f"Error parsing publication date '{date_str}': {e}")
                continue
        
        # If paper isn't new by its own dates, check its notes
        if not is_new:
            try:
                child_items = zot.children(item['key'])
                for child in child_items:
                    if child['data'].get('itemType') == 'note':
                        note_date_str = child['data'].get('dateModified') or child['data'].get('dateAdded')
                        if note_date_str:
                            try:
                                note_date = datetime.fromisoformat(note_date_str.replace("Z", "+00:00"))
                                if note_date > last_date_dt:
                                    is_new = True
                                    logging.info(f"Including paper due to new/modified note from {note_date_str}")
                                    break
                            except Exception as e:
                                logging.error(f"Error parsing note date '{note_date_str}': {e}")
                                continue
            except Exception as e:
                logging.error(f"Error fetching child items for publication {item['key']}: {e}")
                continue

        if is_new:
            new_items.append(item)
            
    return new_items

# ------------------------------------------------------------------------------
# NOTE: this function is deprecated now, because it did not work very well
def replace_names_in_notes(notes, slack_users_df):
    """Replace names in notes with matches from Slack users, inserting user IDs."""
    def find_best_match(name):
        name_cleaned = name.lstrip("@").lower()
        best_match = None
        highest_score = 0
        for _, row in slack_users_df.iterrows():
            normalized_cleaned = row["display_name_normalized"].replace(" ", "").lower()
            score = fuzz.ratio(name_cleaned, normalized_cleaned)
            if score > highest_score and score >= 50:  # Threshold of 50
                highest_score = score
                best_match = row["id"]
        return best_match

    def replacer(match):
        name = match.group(0)
        matched_id = find_best_match(name)
        if matched_id:
            return f"<@{matched_id}>"
        return name

    return re.sub(r"@\w+", replacer, notes)


# NOTE: kept slack_users_df argument for compatibility
def replace_names_in_notes_testing(notes: str, slack_users_df=None) -> str:
    """
    Lowercase all names in notes that start with '@'.
    The slack_users_df argument is ignored (kept for compatibility).
    """
    return re.sub(r"@\w+", lambda m: m.group(0).lower(), notes)

# ------------------------------------------------------------------------------
def get_publication_notes(pub, zot, slack_users_df):
    """
    Retrieve and process notes for a given Zotero publication.
    
    - Fetches child items of the publication.
    - Extracts those with itemType 'note'.
    - Removes HTML tags.
    - Limits the combined string to 3000 characters.
    - Replaces Zotero names with Slack names.
    
    Returns the cleaned notes string (or "No note" if none exist).
    """
    notes = []
    try:
        child_items = zot.children(pub['key'])
    except Exception as e:
        logging.error(f"Error fetching child items for publication {pub['key']}: {e}")
        return "No note"
    
    for child in child_items:
        if child['data'].get('itemType') == 'note':
            raw_note = child['data'].get('note', '')
            clean_note = re.sub(r'<[^>]+>', '', raw_note)  # Remove HTML tags
            notes.append(clean_note)
    
    notes_str = "\n".join(notes)[:3000]  # Combine and limit to 3000 characters
    if not notes_str.strip():
        notes_str = "No note"
    else:
        # Remove trailing newline(s)
        notes_str = notes_str.rstrip("\n")
    
    # Replace Zotero names with Slack names (assuming replace_names_in_notes is defined)
    notes_str = replace_names_in_notes(notes_str, slack_users_df)
    return notes_str


def get_publication_notes_no_slack(pub, zot):
    """
    Retrieve and process notes for a given Zotero publication.
    
    - Fetches child items of the publication.
    - Extracts those with itemType 'note'.
    - Removes HTML tags.
    - Limits the combined string to 3000 characters.
    - Replaces Zotero names with Slack names.
    
    Returns the cleaned notes string (or "No note" if none exist).
    """
    notes = []
    try:
        child_items = zot.children(pub['key'])
    except Exception as e:
        logging.error(f"Error fetching child items for publication {pub['key']}: {e}")
        return "No note"
    
    for child in child_items:
        if child['data'].get('itemType') == 'note':
            raw_note = child['data'].get('note', '')
            clean_note = re.sub(r'<[^>]+>', '', raw_note)  # Remove HTML tags
            notes.append(clean_note)
    
    notes_str = "\n".join(notes)[:3000]  # Combine and limit to 3000 characters
    if not notes_str.strip():
        notes_str = "No note"
    else:
        # Remove trailing newline(s)
        notes_str = notes_str.rstrip("\n")
    
    return notes_str


def create_slack_header(last_date_str, new_count):
    """
    Create a header message with:
      - the current UTC date/time,
      - the elapsed time since the last post,
      - and the count of new publications.
    """
    try:
        last_date = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
    except Exception as e:
        last_date = None
        logging.error(f"Error parsing last_date '{last_date_str}': {e}")

    # Use an offset-aware current datetime in UTC.
    now = datetime.now(timezone.utc)
    if last_date:
        delta = now - last_date
        hours, remainder = divmod(delta.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        header = f":calendar: *{now.strftime('%Y-%m-%d %H:%M:%S UTC')}* - Last update {elapsed_str} ago. "
    else:
        header = f":calendar: *{now.strftime('%Y-%m-%d %H:%M:%S UTC')}* - Last update unknown. "

    if new_count > 0:
        header += f"{new_count} new publication{'s' if new_count != 1 else ''} detected."
    else:
        header += "No new publications detected since last post."
    return header

# ------------------------------------------------------------------------------



def format_publication(pub, zot, slack_users_df):
    """
    Convert a Zotero publication JSON entry into a concise, client-friendly
    formatted summary that includes notes, authors, and publication details.
    The output uses emojis and Slack's link formatting.
    """
    data = pub.get('data', {})

    # Get processed notes (using a helper function that retrieves and cleans notes)
    notes_str = get_publication_notes(pub, zot, slack_users_df)
    # Remove any &nbsp; occurrences
    notes_str = notes_str.replace('&nbsp;', ' ')
    
    # Process authors:
    creators = data.get('creators', [])
    author_names = []
    for author in creators:
        if 'firstName' in author and 'lastName' in author:
            author_names.append(f"{author['firstName']} {author['lastName']}")
        elif 'name' in author:
            author_names.append(author['name'])
    authors_str = ", ".join(author_names)
    
    # Determine publication source based on item type:
    item_type = data.get('itemType', '').lower()
    if item_type == 'journalarticle':
        published_in = data.get('journalAbbreviation', 'Unknown')
    elif item_type == 'preprint':
        published_in = 'Preprint'
    else:
        published_in = data.get('publicationTitle', 'Unknown')
    
    # Get other details:
    pub_date = data.get('date', 'Date missing')
    url = data.get('url', '').strip()  # Remove whitespace
    title = data.get('title', 'Title missing')
    doi = data.get('DOI', '').strip()
    alt_link = pub.get('links', {}).get('alternate', {}).get('href', '')
    added_by = pub.get('meta', {}).get('createdByUser', {}).get('username', 'Unknown')
    
    # Determine how to format the title/link:
    if url:
        # URL is available, use it as the clickable link.
        title_formatted = f"<{url}|{title}.>"
    else:
        # URL not available; try to use DOI.
        if doi:
            doi_url = f"https://doi.org/{doi}"
            title_formatted = f"<{doi_url}|{title}.>"
        else:
            # Neither URL nor DOI available, just bold the title.
            title_formatted = f"*{title}*"
    
    # Construct the final message using Slack formatting.
    details = (
        f":book:{notes_str}. "  # Emoji and notes
        f"{authors_str} "       # Authors string
        f"{title_formatted} "    # Clickable title (or bold title)
        f"{published_in} ({pub_date}) "  # Publication source and date
        f"added by: {added_by}, "          # User who added the item
        f"<{alt_link} | [view on Zotero]>"  # Link to view on Zotero
    )
    
    return details

def format_publication_for_mail(pub, zot):
    """
    Convert a Zotero publication JSON entry into a concise, client-friendly
    formatted summary that includes notes, authors, and publication details.
    The output uses emojis and Slack's link formatting.
    """
    data = pub.get('data', {})

    # Get processed notes (using a helper function that retrieves and cleans notes)
    notes_str = get_publication_notes_no_slack(pub, zot)
    # Remove any &nbsp; occurrences
    notes_str = notes_str.replace('&nbsp;', ' ')
    
    # Process authors:
    creators = data.get('creators', [])
    author_names = []
    for author in creators:
        if 'firstName' in author and 'lastName' in author:
            author_names.append(f"{author['firstName']} {author['lastName']}")
        elif 'name' in author:
            author_names.append(author['name'])
    if len(author_names) > 8:
        author_names = author_names[:4] + ["..."] + author_names[-4:]
    authors_str = ", ".join(author_names)
    
    # Determine publication source based on item type:
    item_type = data.get('itemType', '').lower()
    if item_type == 'journalarticle':
        published_in = data.get('journalAbbreviation', 'Unknown')
    elif item_type == 'preprint':
        published_in = 'Preprint'
    else:
        published_in = data.get('publicationTitle', 'Unknown')
    
    # Get other details:
    pub_date = data.get('date', 'Date missing')
    url = data.get('url', '').strip()  # Remove whitespace
    title = data.get('title', 'Title missing')
    doi = data.get('DOI', '').strip()
    alt_link = pub.get('links', {}).get('alternate', {}).get('href', '')
    added_by = pub.get('meta', {}).get('createdByUser', {}).get('username', 'Unknown')
    
    # Determine how to format the title/link:
    if url:
        # URL is available, use it as the clickable link.
        title_formatted = f"{title}\n{url}"
    else:
        # URL not available; try to use DOI.
        if doi:
            doi_url = f"https://doi.org/{doi}"
            title_formatted = f"{title}\n{doi_url}"
        else:
            # Neither URL nor DOI available, just bold the title.
            title_formatted = f"{title}"
    
    # Construct the final message using Slack formatting.
    detail_str = \
        f"Notes: {notes_str}\n" + \
        f"{authors_str}\n" + \
        f"{title_formatted}\n" + \
        f"{published_in} ({pub_date})\n" + \
        f"added by: {added_by}\n" + \
        f"{alt_link}\n" + \
        "\n"
    
    return detail_str

def format_publication_for_mail_html(pub, zot):
    """
    Convert a Zotero publication JSON entry into HTML-formatted summary
    with proper hyperlinks for email clients.
    """
    data = pub.get('data', {})
    
    # Get processed notes
    notes_str = get_publication_notes_no_slack(pub, zot)
    notes_str = notes_str.replace('&nbsp;', ' ')
    
    # Process authors
    creators = data.get('creators', [])
    author_names = []
    for author in creators:
        if 'firstName' in author and 'lastName' in author:
            author_names.append(f"{author['firstName']} {author['lastName']}")
        elif 'name' in author:
            author_names.append(author['name'])
    if len(author_names) > 8:
        author_names = author_names[:4] + ["..."] + author_names[-4:]
    authors_str = ", ".join(author_names)
    
    # Determine publication source
    item_type = data.get('itemType', '').lower()
    if item_type == 'journalarticle':
        published_in = data.get('journalAbbreviation', 'Unknown')
    elif item_type == 'preprint':
        published_in = 'Preprint'
    else:
        published_in = data.get('publicationTitle', 'Unknown')
    
    # Get other details
    pub_date = data.get('date', 'Date missing')
    url = data.get('url', '').strip()
    title = data.get('title', 'Title missing')
    doi = data.get('DOI', '').strip()
    alt_link = pub.get('links', {}).get('alternate', {}).get('href', '')
    added_by = pub.get('meta', {}).get('createdByUser', {}).get('username', 'Unknown')
    
    # Determine the article URL
    article_url = ""
    if url:
        article_url = url
    elif doi:
        article_url = f"https://doi.org/{doi}"
    
    # Build HTML for this entry
    html = '<div style="margin-bottom: 25px; padding: 15px; border: 1px solid #ddd; border-radius: 5px;">\n'
    
    # Notes
    if notes_str and notes_str != "No note":
        html += f'  <div style="background: #fff3cd; padding: 8px; border-radius: 3px; margin-bottom: 10px;"><strong>Notes:</strong> {notes_str}</div>\n'
    
    # Title (with link if available)
    if article_url:
        html += f'  <div style="font-size: 16px; font-weight: bold; color: #2c5aa0; margin: 10px 0;"><a href="{article_url}" target="_blank" style="color: #2c5aa0; text-decoration: none;">{title}</a></div>\n'
    else:
        html += f'  <div style="font-size: 16px; font-weight: bold; color: #2c5aa0; margin: 10px 0;">{title}</div>\n'
    
    # Authors
    if authors_str:
        html += f'  <div style="color: #666; font-style: italic; margin-bottom: 8px;">{authors_str}</div>\n'
    
    # Journal info
    html += f'  <div style="background: #f5f5f5; padding: 5px 10px; border-radius: 3px; display: inline-block; margin-bottom: 8px;">{published_in} ({pub_date})</div>\n'
    
    # Added by and Zotero link
    html += f'  <div style="color: #666; font-size: 14px; margin-top: 10px;">Added by: <strong>{added_by}</strong>'
    if alt_link:
        html += f' • <a href="{alt_link}" target="_blank" style="color: #1a73e8; text-decoration: none;">View in Zotero</a>'
    html += '</div>\n'
    
    html += '</div>\n'
    
    return html

def create_html_email(publications_html):
    """Create complete HTML email with basic styling"""
    html_email = f"""
<html>
<head>
    <style>
        body {{ 
            font-family: Arial, sans-serif; 
            line-height: 1.5; 
            color: #333; 
            max-width: 800px; 
            margin: 0 auto; 
            padding: 20px; 
        }}
        a {{ color: #1a73e8; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h2 style="color: #2c5aa0; border-bottom: 2px solid #2c5aa0; padding-bottom: 10px;">Zotero Update</h2>
    {publications_html}
</body>
</html>
"""
    return html_email

# ------------------------------------------------------------------------------
# GitHub Integration Functions
# ------------------------------------------------------------------------------

def format_publication_for_github(pub, zot):
    """
    Convert a Zotero publication JSON entry into a GitHub issue format.
    Returns a tuple of (title, body) where body is markdown-formatted.
    """
    data = pub.get('data', {})

    # Get processed notes (without Slack mentions)
    notes_str = get_publication_notes_no_slack(pub, zot)
    notes_str = notes_str.replace('&nbsp;', ' ')

    # Process authors
    creators = data.get('creators', [])
    author_names = []
    for author in creators:
        if 'firstName' in author and 'lastName' in author:
            author_names.append(f"{author['firstName']} {author['lastName']}")
        elif 'name' in author:
            author_names.append(author['name'])
    if len(author_names) > 8:
        author_names = author_names[:4] + ["..."] + author_names[-4:]
    authors_str = ", ".join(author_names)

    # Determine publication source
    item_type = data.get('itemType', '').lower()
    if item_type == 'journalarticle':
        published_in = data.get('journalAbbreviation', 'Unknown')
    elif item_type == 'preprint':
        published_in = 'Preprint'
    else:
        published_in = data.get('publicationTitle', 'Unknown')

    # Get other details
    pub_date = data.get('date', 'Date missing')
    url = data.get('url', '').strip()
    title = data.get('title', 'Title missing')
    doi = data.get('DOI', '').strip()
    alt_link = pub.get('links', {}).get('alternate', {}).get('href', '')
    added_by = pub.get('meta', {}).get('createdByUser', {}).get('username', 'Unknown')
    abstract = data.get('abstractNote', '').strip()

    # Clean up abstract
    if abstract:
        # Remove HTML tags
        abstract = re.sub(r'<[^>]+>', '', abstract)
        # Remove leading "Abstract" word (case-insensitive)
        abstract = re.sub(r'^Abstract\s*', '', abstract, flags=re.IGNORECASE)
        # Normalize whitespace (collapse multiple spaces/newlines)
        abstract = re.sub(r'\s+', ' ', abstract).strip()

    # Truncate title if needed (GitHub limit is 256 chars)
    issue_title = title[:253] + "..." if len(title) > 256 else title

    # Build markdown body
    body_parts = []

    # Notes section
    if notes_str and notes_str != "No note":
        body_parts.append(f"## Notes\n{notes_str}")

    # Abstract section
    if abstract:
        body_parts.append(f"## Abstract\n{abstract}")

    # Publication details
    body_parts.append("## Details")

    if authors_str:
        body_parts.append(f"**Authors:** {authors_str}")

    body_parts.append(f"**Published in:** {published_in} ({pub_date})")

    # Link to article
    if url:
        body_parts.append(f"**URL:** {url}")
    elif doi:
        body_parts.append(f"**DOI:** https://doi.org/{doi}")

    # Zotero link
    if alt_link:
        body_parts.append(f"**Zotero:** {alt_link}")

    body_parts.append(f"**Added by:** {added_by}")

    body = "\n\n".join(body_parts)

    return issue_title, body


def get_github_project_id(token, owner, project_number, is_org=True):
    """
    Get GitHub Project v2 node ID from project number.

    Args:
        token: GitHub PAT
        owner: Organization or user name
        project_number: Project number (from URL)
        is_org: True if owner is an organization, False if user

    Returns:
        Project node ID or None on failure
    """
    if is_org:
        query = """
        query($owner: String!, $number: Int!) {
            organization(login: $owner) {
                projectV2(number: $number) {
                    id
                }
            }
        }
        """
        path = ["data", "organization", "projectV2", "id"]
    else:
        query = """
        query($owner: String!, $number: Int!) {
            user(login: $owner) {
                projectV2(number: $number) {
                    id
                }
            }
        }
        """
        path = ["data", "user", "projectV2", "id"]

    variables = {"owner": owner, "number": int(project_number)}

    result = github_graphql_request(token, query, variables)
    if result:
        try:
            value = result
            for key in path:
                value = value[key]
            return value
        except (KeyError, TypeError) as e:
            # If org query fails, try user query
            if is_org:
                logging.info(f"Organization project not found for {owner}, trying user project...")
                return get_github_project_id(token, owner, project_number, is_org=False)
            logging.error(f"Failed to extract project ID: {e}")
    return None


def github_graphql_request(token, query, variables=None):
    """
    Execute a GitHub GraphQL API request.

    Returns:
        Response JSON or None on failure
    """
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            if "errors" in result:
                logging.error(f"GitHub GraphQL errors: {result['errors']}")
                return None
            return result
    except urllib.error.HTTPError as e:
        logging.error(f"GitHub GraphQL HTTP error {e.code}: {e.read().decode('utf-8')}")
        return None
    except urllib.error.URLError as e:
        logging.error(f"GitHub GraphQL URL error: {e.reason}")
        return None


def check_issue_exists(token, repo, title):
    """
    Check if an issue with the given title already exists in the repository.

    Args:
        token: GitHub PAT
        repo: Repository in "owner/repo" format
        title: Issue title to search for

    Returns:
        Issue node_id if exists, None if not found
    """
    # Use GitHub search API to find issues with exact title
    # We search in the specific repo for issues with the title
    import urllib.parse

    # Search query: exact title match in repo
    query = f'repo:{repo} is:issue "{title}" in:title'
    encoded_query = urllib.parse.quote(query)
    url = f"https://api.github.com/search/issues?q={encoded_query}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    req = urllib.request.Request(url, headers=headers, method='GET')

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            items = result.get("items", [])

            # Check for exact title match (search API does fuzzy matching)
            for item in items:
                if item.get("title") == title:
                    logging.info(f"Issue already exists: #{item.get('number')} - {title[:50]}...")
                    return item.get("node_id")

            return None
    except urllib.error.HTTPError as e:
        logging.warning(f"Failed to search for existing issues: HTTP {e.code}")
        return None
    except urllib.error.URLError as e:
        logging.warning(f"Failed to search for existing issues: {e.reason}")
        return None


def create_github_issue(token, repo, title, body):
    """
    Create a GitHub issue using the REST API.

    Args:
        token: GitHub PAT
        repo: Repository in "owner/repo" format
        title: Issue title
        body: Issue body (markdown)

    Returns:
        Issue node_id for project linking, or None on failure
    """
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    payload = {
        "title": title,
        "body": body
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            node_id = result.get("node_id")
            issue_number = result.get("number")
            logging.info(f"Created GitHub issue #{issue_number} in {repo}")
            return node_id
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        logging.error(f"Failed to create GitHub issue: HTTP {e.code} - {error_body}")
        return None
    except urllib.error.URLError as e:
        logging.error(f"Failed to create GitHub issue: {e.reason}")
        return None


def add_issue_to_project(token, project_id, issue_node_id):
    """
    Add an issue to a GitHub Project (v2).

    Args:
        token: GitHub PAT
        project_id: Project node ID
        issue_node_id: Issue node ID

    Returns:
        True on success, False on failure
    """
    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
        addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item {
                id
            }
        }
    }
    """

    variables = {
        "projectId": project_id,
        "contentId": issue_node_id
    }

    result = github_graphql_request(token, mutation, variables)
    if result and result.get("data", {}).get("addProjectV2ItemById", {}).get("item"):
        logging.info(f"Added issue to GitHub project")
        return True
    else:
        logging.error(f"Failed to add issue to GitHub project")
        return False


def post_to_github(pub, zot, token, repo, project_id):
    """
    Create a GitHub issue for a publication and add it to a project.
    Skips creation if an issue with the same title already exists.

    Args:
        pub: Zotero publication data
        zot: Zotero API client
        token: GitHub PAT
        repo: Target repository (owner/repo format)
        project_id: GitHub Project node ID

    Returns:
        True on success (or if already exists), False on failure
    """
    # Format the publication
    title, body = format_publication_for_github(pub, zot)

    # Check if issue already exists
    existing_node_id = check_issue_exists(token, repo, title)
    if existing_node_id:
        logging.info(f"Skipping duplicate: {title[:50]}...")
        # Still try to add to project in case it's not there yet
        if project_id:
            add_issue_to_project(token, project_id, existing_node_id)
        return True  # Not a failure, just already exists

    # Create the issue
    issue_node_id = create_github_issue(token, repo, title, body)
    if not issue_node_id:
        return False

    # Add to project
    if project_id:
        add_issue_to_project(token, project_id, issue_node_id)

    return True


# ------------------------------------------------------------------------------
def retry_with_backoff(func, max_retries=5, initial_delay=1):
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Function to retry
        max_retries: Maximum number of retries
        initial_delay: Initial delay in seconds
    """
    for attempt in range(max_retries):
        try:
            return func()
        except SlackApiError as e:
            if e.response.get("error") == "ratelimited":
                if attempt == max_retries - 1:
                    raise  # Last attempt failed
                delay = initial_delay * (2 ** attempt)  # Exponential backoff
                logging.warning(f"Rate limited by Slack API. Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise

def post_to_slack(token, channel, header_message, publication_messages):
    """
    Post a message to Slack that includes a header (with current timestamp, elapsed time, and count)
    and then posts each publication message individually with a 0.5 second delay between posts.

    Returns a tuple (success_count, failure_count).
    """
    client = WebClient(token=token)
    success_count = 0
    failure_count = 0

    # Attempt to join the channel (for public channels)
    def join_channel():
        return client.conversations_join(channel=channel)

    try:
        join_response = retry_with_backoff(join_channel)
        if join_response.get("ok"):
            logging.info(f"Joined channel {channel} successfully.")
    except SlackApiError as e:
        if e.response.get("error") == "already_in_channel":
            logging.info(f"Already a member of channel {channel}.")
        else:
            logging.error(f"Error joining channel {channel}: {e.response.get('error')}")
    
    # Post the header message first.
    def post_header():
        return client.chat_postMessage(channel=channel, text=header_message)

    try:
        response = retry_with_backoff(post_header)
        if response.get("ok"):
            success_count += 1
            logging.info("Posted header message to Slack successfully.")
        else:
            failure_count += 1
            logging.error("Failed to post header message: " + str(response))
    except SlackApiError as e:
        failure_count += 1
        logging.error("Error posting header message: " + e.response.get("error"))

    # Post each publication message individually with a delay
    for pub_msg in publication_messages:
        time.sleep(0.5)  # Pause for 0.5 seconds between posts
        def post_message():
            return client.chat_postMessage(channel=channel, text=pub_msg)

        try:
            response = retry_with_backoff(post_message)
            if response.get("ok"):
                success_count += 1
                logging.info("Posted publication message to Slack successfully.")
            else:
                failure_count += 1
                logging.error("Failed to post publication message: " + str(response))
        except SlackApiError as e:
            failure_count += 1
            logging.error("Error posting publication message: " + e.response.get("error"))
    
    return success_count, failure_count

def get_slack_users_depr(slack_token):
    """Fetch Slack users and return a DataFrame with display_name_normalized and id."""
    client = WebClient(token=slack_token)
    
    def fetch_users():
        return client.users_list()
    
    response = retry_with_backoff(fetch_users)

    members = []
    for member in response.get('members', []):
        if not member.get('deleted'):
            profile = member.get("profile", {})
            display_name = profile.get("display_name_normalized")
            user_id = member.get("id")
            if display_name and user_id:
                members.append({"display_name_normalized": display_name, "id": user_id})

    # Convert the list of members to a DataFrame
    members_df = pd.DataFrame(members)
    return members_df

def get_slack_users_df(slack_ids_url):
    """Fetch Slack users and return a DataFrame with display_name_normalized and id."""
    slack_users_df = pd.read_csv(slack_ids_url)
    slack_users_df = slack_users_df[["Names", "ID"]].rename(columns={"Names": "display_name_normalized", "ID": "id"})
    logging.info(f"Loaded slack user df with shape: {slack_users_df.shape}")
    return slack_users_df

# ------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Post new Zotero publications to Slack using WebClient")
    parser.add_argument("--file_path", required=True,
                        help="Path to the state CSV file with columns: subcollectionID, lastDate, channel")
    parser.add_argument("--zotero_api_key", required=True, help="Zotero API key.")
    parser.add_argument("--zotero_library_id", required=True, help="Zotero library ID.")
    parser.add_argument("--slack_token", required=True,
                        help="Slack Bot User OAuth token (with scopes: chat:write, conversations:join, users:read)")
    parser.add_argument("--gmail_password", required=True,
                        help="Password needed to send mails from saezlab.zotero@gmail.com")
    parser.add_argument("--receiver_mails", required=True,
                        help="Mails that get Zotero updates")
    parser.add_argument("--slack_ids_url", required=True,
                        help="Link to Google sheets that stores the matching between names and slack user IDs")
    parser.add_argument("--test", action="store_true",
                        help="Run in test mode (log formatted publications instead of posting, and do not update state file)")
    parser.add_argument("--github_token", required=False, default=None,
                        help="GitHub PAT with repo and project scopes (optional)")
    parser.add_argument("--github_repo", required=False, default=None,
                        help="Target repository for GitHub issues (owner/repo format)")
    parser.add_argument("--github_project_number", required=False, default=None,
                        help="GitHub Project number (from project URL)")
    args = parser.parse_args()

    # Validate inputs
    try:
        validate_inputs(args.file_path, args.zotero_api_key, args.zotero_library_id, args.slack_token)
    except Exception as e:
        logging.error(f"Input validation failed: {e}")
        print(f"Input validation failed: {e}")
        sys.exit(1)
        return

    # Initialize Zotero API (assuming library type 'group'; adjust if needed)
    zot = zotero.Zotero(args.zotero_library_id, 'group', args.zotero_api_key)

    # Initialize GitHub project ID if GitHub posting is enabled
    github_project_id = None
    github_enabled = args.github_token and args.github_repo
    if github_enabled:
        logging.info(f"GitHub integration enabled for repo: {args.github_repo}")
        if args.github_project_number:
            owner = args.github_repo.split('/')[0]
            github_project_id = get_github_project_id(args.github_token, owner, args.github_project_number)
            if github_project_id:
                logging.info(f"GitHub project ID resolved: {github_project_id}")
            else:
                logging.warning(f"Could not resolve GitHub project ID for project #{args.github_project_number}. Issues will be created but not added to project.")
        else:
            logging.info("No GitHub project number provided. Issues will be created but not added to any project.")

    # Read state file
    try:
        state_df = pd.read_csv(args.file_path)
    except Exception as e:
        logging.error(f"Failed to read state file '{args.file_path}': {e}")
        print(f"Failed to read state file: {e}")
        sys.exit(1)
        return

    # Verify state file contains required columns.
    for col in ['subcollectionID', 'lastDate', 'channel']:
        if col not in state_df.columns:
            msg = f"State file is missing required column: {col}"
            logging.error(msg)
            print(msg)
            return

    # Fetch Slack users for name replacement in notes (if needed)
    if args.test:
        # In test mode, create an empty DataFrame to skip Slack user fetching
        slack_users_df = pd.DataFrame(columns=['display_name_normalized', 'id'])
        logging.info("Test mode: Skipping Slack user fetching")
    else:
        #slack_users_df = get_slack_users_df(args.slack_token)
        slack_users_df = get_slack_users_df(args.slack_ids_url)

    total_success = 0
    total_failure = 0
    updated_last_dates = []

    # Process each subcollection from the state file
    for index, row in state_df.iterrows():
        subcollection_id = row['subcollectionID']
        last_date = row['lastDate']  # ISO-formatted string from the state file
        channel = row['channel']

        logging.info(f"Processing subcollection '{subcollection_id}' for channel '{channel}' with lastDate {last_date}.")
        print(f"Processing subcollection {subcollection_id}...")

        # Fetch new publications for this subcollection
        try:
            new_pubs = fetch_new_publications(zot, subcollection_id, last_date)
        except Exception as e:
            logging.error(f"Aborting due to error in fetching publications: {e}")
            sys.exit(1)
        new_count = len(new_pubs)
        logging.info(f"Found {new_count} new publications in subcollection '{subcollection_id}'.")
        print(f"Found {new_count} new publications.")

        # Create a header message
        header_message = create_slack_header(last_date, new_count)
        
        # Format publication details if new items exist
        if new_count > 0:
            formatted_publications = [format_publication(pub, zot, slack_users_df) for pub in new_pubs]
        else:
            formatted_publications = []

        # Post to Slack (or log in test mode)
        if args.test:
            logging.info("Test Mode - Message to be posted:")
            logging.info(header_message)
            for pub_msg in formatted_publications:
                logging.info(pub_msg)
            success_count = new_count  # assume success
            failure_count = 0
        else:
            success_count, failure_count = post_to_slack(args.slack_token, channel, header_message, formatted_publications)

            # Compose mail
            receiver_email_list = args.receiver_mails.split(";")

            # 1) setup
            today = datetime.now().date()

            # Generate HTML for all publications
            publications_html = "".join([format_publication_for_mail_html(pub, zot=zot) for pub in new_pubs])
            html_email_content = create_html_email(publications_html)

            # Generate plain text as fallback
            plain_text = "----------\n".join([format_publication_for_mail(pub, zot=zot) for pub in new_pubs])

            sender_email = "saezlab.zotero@gmail.com"
            subject = f"{str(today)} Zotero Update"
            app_password = args.gmail_password

            # 2) create and send message
            for receiver_email in receiver_email_list:
                msg = MIMEMultipart("alternative")  # Changed to "alternative" for HTML
                msg["From"] = sender_email
                msg["To"] = receiver_email
                msg["Subject"] = subject
                
                # Attach both plain text and HTML versions
                part1 = MIMEText(plain_text, "plain")
                part2 = MIMEText(html_email_content, "html")
                
                msg.attach(part1)
                msg.attach(part2)

                # 3) send the mail
                with smtplib.SMTP(host="smtp.gmail.com", port=587) as server:
                    server.starttls()
                    server.login(sender_email, app_password)
                    server.send_message(msg)

            # Post to GitHub (if enabled and not in test mode)
            if github_enabled and new_pubs:
                logging.info(f"Posting {len(new_pubs)} publications to GitHub...")
                github_success = 0
                github_failure = 0
                for pub in new_pubs:
                    if post_to_github(pub, zot, args.github_token, args.github_repo, github_project_id):
                        github_success += 1
                    else:
                        github_failure += 1
                    time.sleep(0.5)  # Rate limiting
                logging.info(f"GitHub posting complete: {github_success} success, {github_failure} failures")

        total_success += success_count
        total_failure += failure_count

        # Update the lastDate in the state file (if new publications were found)
        if new_pubs:
            pub_dates = []
            for pub in new_pubs:
                # Get paper dates
                data = pub.get('data', {})
                date_str = data.get('dateModified') or data.get('dateAdded')
                if date_str:
                    try:
                        pub_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        pub_dates.append(pub_date)
                    except Exception as e:
                        logging.error(f"Error parsing publication date '{date_str}': {e}")
                
                # Get note dates
                try:
                    child_items = zot.children(pub['key'])
                    for child in child_items:
                        if child['data'].get('itemType') == 'note':
                            note_date_str = child['data'].get('dateModified') or child['data'].get('dateAdded')
                            if note_date_str:
                                try:
                                    note_date = datetime.fromisoformat(note_date_str.replace("Z", "+00:00"))
                                    pub_dates.append(note_date)
                                except Exception as e:
                                    logging.error(f"Error parsing note date '{note_date_str}': {e}")
                except Exception as e:
                    logging.error(f"Error fetching child items for publication {pub['key']}: {e}")

            if pub_dates:
                max_pub_date = max(pub_dates)  # Use the most recent date from either papers or notes
                new_last_date = max_pub_date.isoformat()
                if new_last_date.endswith("+00:00"):
                    new_last_date = new_last_date.replace("+00:00", "Z")
                logging.info(f"Updated lastDate to {new_last_date} based on most recent paper or note date")
            else:
                new_last_date = last_date
        else:
            new_last_date = last_date

        updated_last_dates.append(new_last_date)


    report_msg = f"Total publications posted: {total_success}, Failures: {total_failure}"
    logging.info(report_msg)
    print(report_msg)

    # Update state file only if not running in test mode.
    if not args.test:
        state_df["lastDate"] = updated_last_dates
        try:
            state_df.to_csv(args.file_path, index=False)
            logging.info(f"State file '{args.file_path}' updated successfully.")
            print("State file updated successfully.")
        except Exception as e:
            logging.error(f"Failed to update state file: {e}")
            print(f"Failed to update state file: {e}")
            sys.exit(1)
    else:
        logging.info("Test mode enabled; state file not updated.")
        print("Test mode: state file not updated.")

if __name__ == "__main__":
    main()
