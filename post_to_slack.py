#!/usr/bin/env python3
import argparse
import pandas as pd
import logging
import re
from fuzzywuzzy import fuzz # match slack names and zotero mentiones
from datetime import datetime
from pyzotero import zotero
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

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
    Returns only items with a publication date (dateModified or dateAdded)
    later than the state file’s last_date.
    """
    new_items = []
    try:
        last_date_dt = parse_last_date(last_date)
    except Exception as e:
        logging.error(f"Error parsing last_date: {e}")
        return new_items

    try:
        items = zot.collection_items_top(collection_id, limit=100, sort='dateAdded', direction='desc')
    except Exception as e:
        logging.error(f"Error fetching items from collection '{collection_id}': {e}")
        return new_items

    for item in items:
        data = item.get('data', {})
        # Prefer dateModified; fallback to dateAdded.
        date_str = data.get('dateModified') or data.get('dateAdded')
        if not date_str:
            continue
        try:
            pub_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception as e:
            logging.error(f"Error parsing publication date '{date_str}': {e}")
            continue

        if pub_date > last_date_dt:
            new_items.append(item)
    return new_items

# ------------------------------------------------------------------------------
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

def get_publication_notes(pub, zot, slack_users):
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
    notes_str = replace_names_in_notes(notes_str, slack_users)
    return notes_str


# ------------------------------------------------------------------------------



def format_publication(pub, zot, slack_users):
    """
    Convert a Zotero publication JSON entry into a concise, client-friendly
    formatted summary that includes notes, authors, and publication details.
    The output uses emojis and Slack's link formatting.
    """
    data = pub.get('data', {})

    # Get processed notes (using a helper function that retrieves and cleans notes)
    notes_str = get_publication_notes(pub, zot, slack_users)
    
    # Process authors:
    creators = data.get('creators', [])
    author_names = []
    for author in creators[:3]:
        if 'firstName' in author and 'lastName' in author:
            author_names.append(f"{author['firstName']} {author['lastName']}")
        elif 'name' in author:
            author_names.append(author['name'])
    if len(creators) > 3:
        author_names.append("et al")
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
    url = data.get('url', 'No URL')
    title = data.get('title', 'Title missing')
    alt_link = pub.get('links', {}).get('alternate', {}).get('href', '')
    added_by = pub.get('meta', {}).get('createdByUser', {}).get('username', 'Unknown')
    
    # Construct the final message using Slack formatting.
    details = (
        f":book:{notes_str}. "  # Emoji and notes
        f"{authors_str} "  # Authors string
        f"<{url}|{title}.> "  # Slack link to the publication (displaying the title)
        f"{published_in} ({pub_date}) "  # Publication source and date
        f"added by: {added_by}, "  # User who added the item
        f"<{alt_link} | [view on Zotero]>"  # Link to view on Zotero
    )
    
    return details


# ------------------------------------------------------------------------------
def post_to_slack(token, channel, formatted_publications):
    """
    Post formatted publication summaries to Slack using the WebClient.
    Attempts to join the channel if not already a member.
    For private channels, ensure the bot is added manually.
    Returns a tuple (success_count, failure_count).
    """
    client = WebClient(token=token)
    success_count = 0
    failure_count = 0

    # Attempt to join the channel (for public channels)
    # try:
    #     join_response = client.conversations_join(channel=channel)
    #     if join_response.get("ok"):
    #         logging.info(f"Joined channel {channel} successfully.")
    # except SlackApiError as e:
    #     # If already in channel, that's fine.
    #     if e.response.get("error") == "already_in_channel":
    #         logging.info(f"Already a member of channel {channel}.")
    #     else:
    #         logging.error(f"Error joining channel {channel}: {e.response.get('error')}")

    for text in formatted_publications:
        try:
            response = client.chat_postMessage(channel=channel, text=text)
            if response.get("ok"):
                success_count += 1
                logging.info("Posted publication to Slack successfully.")
            else:
                failure_count += 1
                logging.error(f"Failed to post publication: {response}")
        except SlackApiError as e:
            failure_count += 1
            logging.error(f"Error posting message: {e.response.get('error')}")
    return success_count, failure_count

def get_slack_users(slack_token):
    """Fetch Slack users and return a DataFrame with display_name_normalized and id."""
    client = WebClient(token=slack_token)
    response = client.users_list()

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

# ------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Post new Zotero publications to Slack using WebClient")
    parser.add_argument("--file_path", required=True,
                        help="Path to the state CSV file with columns: subcollectionID, lastDate, channel")
    parser.add_argument("--zotero_api_key", required=True, help="Zotero API key.")
    parser.add_argument("--zotero_library_id", required=True, help="Zotero library ID.")
    parser.add_argument("--slack_token", required=True,
                        help="Slack Bot User OAuth token (with scopes: chat:write, conversations:join, users:read)")
    parser.add_argument("--test", action="store_true",
                        help="Run in test mode (log formatted publications instead of posting, and do not update state file)")
    args = parser.parse_args()

    # Validate inputs
    try:
        validate_inputs(args.file_path, args.zotero_api_key, args.zotero_library_id, args.slack_token)
    except Exception as e:
        logging.error(f"Input validation failed: {e}")
        print(f"Input validation failed: {e}")
        return

    # Initialize Zotero API (assuming library type 'group'; adjust if needed)
    zot = zotero.Zotero(args.zotero_library_id, 'group', args.zotero_api_key)

    # Read state file
    try:
        state_df = pd.read_csv(args.file_path)
    except Exception as e:
        logging.error(f"Failed to read state file '{args.file_path}': {e}")
        print(f"Failed to read state file: {e}")
        return

    # Verify state file contains required columns.
    for col in ['subcollectionID', 'lastDate', 'channel']:
        if col not in state_df.columns:
            msg = f"State file is missing required column: {col}"
            logging.error(msg)
            print(msg)
            return

    # Fetch Slack users for name replacement in notes (if needed)
    slack_users = get_slack_users(args.slack_token)

    total_success = 0
    total_failure = 0
    updated_last_dates = []

    for _, row in state_df.iterrows():
        subcollection_id = row['subcollectionID']
        last_date = row['lastDate']
        channel = row['channel']

        logging.info(f"Processing subcollection '{subcollection_id}' for channel '{channel}' with lastDate {last_date}.")
        print(f"Processing subcollection {subcollection_id}...")

        # Fetch new publications from Zotero for this subcollection.
        new_pubs = fetch_new_publications(zot, subcollection_id, last_date)
        logging.info(f"Found {len(new_pubs)} new publications in subcollection '{subcollection_id}'.")
        print(f"Found {len(new_pubs)} new publications.")

        # Format publications for posting.
        formatted_publications = [format_publication(pub, zot, slack_users) for pub in new_pubs]

        if args.test:
            for formatted in formatted_publications:
                logging.info("Test Mode - Formatted Publication:\n" + formatted)
            # In test mode, assume all posts would be successful.
            success_count = len(formatted_publications)
            failure_count = 0
        else:
            success_count, failure_count = post_to_slack(args.slack_token, channel, formatted_publications)

        total_success += success_count
        total_failure += failure_count

        # Update lastDate: choose the maximum publication date from the new items.
        if new_pubs:
            pub_dates = []
            for pub in new_pubs:
                data = pub.get('data', {})
                date_str = data.get('dateModified') or data.get('dateAdded')
                if date_str:
                    try:
                        pub_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        pub_dates.append(pub_date)
                    except Exception as e:
                        logging.error(f"Error parsing publication date '{date_str}': {e}")
            if pub_dates:
                max_pub_date = max(pub_dates)
                new_last_date = max_pub_date.isoformat()
                if new_last_date.endswith("+00:00"):
                    new_last_date = new_last_date.replace("+00:00", "Z")
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
    else:
        logging.info("Test mode enabled; state file not updated.")
        print("Test mode: state file not updated.")

if __name__ == "__main__":
    main()
