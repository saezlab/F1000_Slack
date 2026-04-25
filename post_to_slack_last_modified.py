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
def parse_receiver_email_list(receiver_mails, subcollection_id):
    """Parse a semicolon-separated list of recipient emails for one subcollection."""
    if pd.isna(receiver_mails):
        raise ValueError(
            f"State file row for subcollection '{subcollection_id}' is missing receiverMails."
        )

    receiver_email_list = [
        email.strip() for email in str(receiver_mails).split(";") if email.strip()
    ]
    if not receiver_email_list:
        raise ValueError(
            f"State file row for subcollection '{subcollection_id}' has no valid receiverMails."
        )

    return receiver_email_list

# ------------------------------------------------------------------------------
def mask_email_for_logging(email):
    """Mask most of an email address while keeping it recognizable in logs."""
    email = str(email).strip()
    if "@" not in email:
        visible_chars = min(3, len(email))
        return email[:visible_chars] + "*" * max(0, len(email) - visible_chars)

    local_part, _, domain = email.partition("@")
    visible_local_chars = min(3, len(local_part))
    masked_local = local_part[:visible_local_chars] + "*" * max(0, len(local_part) - visible_local_chars)

    domain_name, dot, tld = domain.rpartition(".")
    if dot:
        visible_domain_chars = min(3, len(domain_name))
        masked_domain_name = domain_name[:visible_domain_chars] + "*" * max(0, len(domain_name) - visible_domain_chars)
        masked_domain = f"{masked_domain_name}.{tld}"
    else:
        visible_domain_chars = min(3, len(domain))
        masked_domain = domain[:visible_domain_chars] + "*" * max(0, len(domain) - visible_domain_chars)

    return f"{masked_local}@{masked_domain}"

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
def parse_zotero_date(date_str, context):
    """Parse a Zotero ISO date string and log context on failure."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception as e:
        logging.error(f"Error parsing {context} date '{date_str}': {e}")
        return None


def get_item_modified_date(item):
    """Return the best modified timestamp for a Zotero item."""
    data = item.get('data', {})
    date_str = data.get('dateModified') or data.get('dateAdded')
    item_key = item.get('key') or data.get('key', 'unknown')
    return parse_zotero_date(date_str, f"item {item_key}")


def normalize_single_item(item_response):
    """Pyzotero item lookups may return a dict or a one-item list."""
    if isinstance(item_response, list):
        if not item_response:
            return None
        item_response = item_response[0]
    if item_response and 'key' not in item_response and item_response.get('data', {}).get('key'):
        item_response['key'] = item_response['data']['key']
    return item_response


def fetch_modified_collection_top_items(zot, collection_id, last_date_dt, limit=100):
    """
    Fetch top-level collection items sorted by dateModified, stopping once a
    page reaches items that are not newer than the cutoff.
    """
    modified_items = []
    start = 0

    while True:
        items = zot.collection_items_top(
            collection_id,
            limit=limit,
            start=start,
            sort='dateModified',
            direction='desc'
        )
        if not items:
            break

        should_continue = True
        for item in items:
            item_date = get_item_modified_date(item)
            if not item_date:
                continue
            if item_date > last_date_dt:
                modified_items.append(item)
            else:
                should_continue = False

        if len(items) < limit or not should_continue:
            break
        start += limit

    return modified_items


def fetch_modified_notes(zot, last_date_dt, limit=100):
    """
    Fetch library note items sorted by dateModified, stopping once notes are
    not newer than the cutoff.
    """
    modified_notes = []
    start = 0

    while True:
        notes = zot.items(
            itemType='note',
            limit=limit,
            start=start,
            sort='dateModified',
            direction='desc'
        )
        if not notes:
            break

        should_continue = True
        for note in notes:
            note_date = get_item_modified_date(note)
            if not note_date:
                continue
            if note_date > last_date_dt:
                modified_notes.append(note)
            else:
                should_continue = False

        if len(notes) < limit or not should_continue:
            break
        start += limit

    return modified_notes


def parent_in_collection(parent_item, collection_id):
    """Return whether a parent Zotero item belongs to the target collection."""
    collections = parent_item.get('data', {}).get('collections', [])
    return collection_id in collections


def fetch_new_publications(zot, collection_id, last_date):
    """
    Fetch modified publications from a specific Zotero subcollection.
    Returns a tuple of:
    - publications whose own dateModified/dateAdded is later than last_date
    - publications with child notes whose dateModified/dateAdded is later than last_date
    - the latest trigger timestamp seen across those publications and notes
    """
    modified_items_by_key = {}
    latest_change_date = None
    try:
        last_date_dt = parse_last_date(last_date)
    except Exception as e:
        logging.error(f"Error parsing last_date: {e}")
        raise  # abort if last_date cannot be parsed

    try:
        top_items = fetch_modified_collection_top_items(zot, collection_id, last_date_dt)
    except Exception as e:
        logging.error(f"Error fetching modified top-level items from collection '{collection_id}': {e}")
        raise

    for item in top_items:
        item_key = item.get('key') or item.get('data', {}).get('key')
        item_date = get_item_modified_date(item)
        if not item_key or not item_date:
            continue
        modified_items_by_key[item_key] = item
        latest_change_date = max(latest_change_date, item_date) if latest_change_date else item_date
        logging.info(f"Including paper {item_key} due to modified top-level item from {item_date.isoformat()}")

    try:
        modified_notes = fetch_modified_notes(zot, last_date_dt)
    except Exception as e:
        logging.error(f"Error fetching modified note items: {e}")
        raise

    parent_cache = {}
    for note in modified_notes:
        note_data = note.get('data', {})
        if note_data.get('itemType') != 'note':
            continue

        parent_key = note_data.get('parentItem')
        if not parent_key:
            logging.info(f"Skipping modified standalone note {note.get('key', 'unknown')} with no parentItem")
            continue

        note_date = get_item_modified_date(note)
        if not note_date:
            continue

        if parent_key not in parent_cache:
            try:
                parent_cache[parent_key] = normalize_single_item(zot.item(parent_key))
            except Exception as e:
                logging.error(f"Error fetching parent item {parent_key} for note {note.get('key', 'unknown')}: {e}")
                continue

        parent_item = parent_cache.get(parent_key)
        if not parent_item:
            logging.info(f"Skipping modified note {note.get('key', 'unknown')} because parent {parent_key} was not found")
            continue
        if not parent_in_collection(parent_item, collection_id):
            logging.info(f"Skipping modified note {note.get('key', 'unknown')} because parent {parent_key} is outside collection {collection_id}")
            continue

        modified_items_by_key[parent_key] = parent_item
        latest_change_date = max(latest_change_date, note_date) if latest_change_date else note_date
        logging.info(f"Including paper {parent_key} due to new/modified note from {note_date.isoformat()}")

    return list(modified_items_by_key.values()), latest_change_date

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
    parser = argparse.ArgumentParser(description="Shadow bot for Zotero publications modified since the last run")
    parser.add_argument("--file_path", required=True,
                        help="Path to the state CSV file with columns: subcollectionID, lastDate, channel, receiverMails")
    parser.add_argument("--zotero_api_key", required=True, help="Zotero API key.")
    parser.add_argument("--zotero_library_id", required=True, help="Zotero library ID.")
    parser.add_argument("--slack_token", required=True,
                        help="Slack Bot User OAuth token (with scopes: chat:write, channels:join, users:read)")
    parser.add_argument("--gmail_password", required=True,
                        help="Password needed to send mails from saezlab.zotero@gmail.com")
    parser.add_argument("--slack_ids_url", required=True,
                        help="Link to Google sheets that stores the matching between names and slack user IDs")
    parser.add_argument("--test", action="store_true",
                        help="Run in test mode (log formatted publications instead of posting, and do not update state file)")
    parser.add_argument("--log_only", action="store_true",
                        help="Log would-be Slack/email messages instead of sending them, but still update the state file")
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

    # Read state file
    try:
        state_df = pd.read_csv(args.file_path)
    except Exception as e:
        logging.error(f"Failed to read state file '{args.file_path}': {e}")
        print(f"Failed to read state file: {e}")
        sys.exit(1)
        return

    # Verify state file contains required columns.
    for col in ['subcollectionID', 'lastDate', 'channel', 'receiverMails']:
        if col not in state_df.columns:
            msg = f"State file is missing required column: {col}"
            logging.error(msg)
            print(msg)
            sys.exit(1)

    receiver_email_lists = []
    for _, row in state_df.iterrows():
        subcollection_id = row['subcollectionID']
        try:
            receiver_email_lists.append(
                parse_receiver_email_list(row['receiverMails'], subcollection_id)
            )
        except ValueError as e:
            logging.error(str(e))
            print(str(e))
            sys.exit(1)

    # Fetch Slack users for name replacement in notes (if needed)
    if args.test or args.log_only:
        # In test/log-only mode, create an empty DataFrame to avoid external Slack ID fetching.
        slack_users_df = pd.DataFrame(columns=['display_name_normalized', 'id'])
        logging.info("Test/log-only mode: Skipping Slack user fetching")
    else:
        #slack_users_df = get_slack_users_df(args.slack_token)
        slack_users_df = get_slack_users_df(args.slack_ids_url)

    total_publications_found = 0
    total_success = 0
    total_failure = 0
    total_emails_sent = 0
    updated_last_dates = []

    # Process each subcollection from the state file
    for row_position, (_, row) in enumerate(state_df.iterrows()):
        subcollection_id = row['subcollectionID']
        last_date = row['lastDate']  # ISO-formatted string from the state file
        channel = row['channel']

        logging.info(f"Processing subcollection '{subcollection_id}' for channel '{channel}' with lastDate {last_date}.")
        print(f"Processing subcollection {subcollection_id}...")

        # Fetch modified publications for this subcollection
        try:
            new_pubs, latest_change_date = fetch_new_publications(zot, subcollection_id, last_date)
        except Exception as e:
            logging.error(f"Aborting due to error in fetching publications: {e}")
            sys.exit(1)
        new_count = len(new_pubs)
        total_publications_found += new_count
        logging.info(f"Found {new_count} new publications in subcollection '{subcollection_id}'.")
        print(f"Found {new_count} new publications.")

        success_count = 0
        failure_count = 0

        if new_count > 0:
            header_message = create_slack_header(last_date, new_count)
            formatted_publications = [format_publication(pub, zot, slack_users_df) for pub in new_pubs]

            # Post to Slack (or log without sending)
            if args.test or args.log_only:
                mode_label = "Test Mode" if args.test else "Log-only Mode"
                logging.info(f"{mode_label} - Message that would be posted:")
                logging.info(header_message)
                for pub_msg in formatted_publications:
                    logging.info(pub_msg)
                if args.log_only:
                    receiver_email_list = receiver_email_lists[row_position]
                    masked_receivers = [mask_email_for_logging(receiver_email) for receiver_email in receiver_email_list]
                    logging.info(f"Log-only Mode - Emails that would be sent for channel '{channel}' to {masked_receivers}.")
                success_count = 0
            else:
                success_count, failure_count = post_to_slack(args.slack_token, channel, header_message, formatted_publications)

                # Compose mail
                receiver_email_list = receiver_email_lists[row_position]

                # 1) setup
                today = datetime.now().date()

                # Generate HTML for all publications
                publications_html = "".join([format_publication_for_mail_html(pub, zot=zot) for pub in new_pubs])
                html_email_content = create_html_email(publications_html)

                # Generate plain text as fallback
                plain_text = "----------\n".join([format_publication_for_mail(pub, zot=zot) for pub in new_pubs])

                sender_email = "saezlab.zotero@gmail.com"
                subject = f"{str(today)} Zotero Update - {channel}"
                app_password = args.gmail_password

                # 2) create and send message
                for receiver_email in receiver_email_list:
                    masked_receiver_email = mask_email_for_logging(receiver_email)
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
                    logging.info(
                        f"Sending email for channel '{channel}' to '{masked_receiver_email}' with subject '{subject}'."
                    )
                    with smtplib.SMTP(host="smtp.gmail.com", port=587) as server:
                        server.starttls()
                        server.login(sender_email, app_password)
                        send_result = server.send_message(msg)

                    if send_result:
                        masked_send_result = {
                            mask_email_for_logging(refused_email): refusal_details
                            for refused_email, refusal_details in send_result.items()
                        }
                        logging.warning(
                            f"Email send returned refused recipients for channel '{channel}': {masked_send_result}"
                        )
                    else:
                        total_emails_sent += 1
                        logging.info(
                            f"Email sent successfully for channel '{channel}' to '{masked_receiver_email}'."
                        )
        else:
            logging.info(
                f"No new publications for subcollection '{subcollection_id}'; skipping Slack and email."
            )
            print("No new publications; skipping Slack and email.")

        total_success += success_count
        total_failure += failure_count

        # Update the lastDate in the state file based on the latest detected trigger.
        if new_pubs and latest_change_date:
            new_last_date = latest_change_date.isoformat()
            if new_last_date.endswith("+00:00"):
                new_last_date = new_last_date.replace("+00:00", "Z")
            logging.info(f"Updated lastDate to {new_last_date} based on most recent modified paper or note")
        else:
            new_last_date = last_date

        updated_last_dates.append(new_last_date)


    summary_messages = [
        f"Total new publications found: {total_publications_found}",
        f"Total Slack messages sent: {total_success}",
        f"Total emails sent: {total_emails_sent}",
        f"Total Slack posting failures: {total_failure}",
    ]
    for summary_message in summary_messages:
        logging.info(summary_message)
        print(summary_message)

    # Update state file only if not running in test mode. Log-only mode still updates state.
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
