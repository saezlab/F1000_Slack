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
        slack_users_df = get_slack_users_df(args.slack_token)

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
