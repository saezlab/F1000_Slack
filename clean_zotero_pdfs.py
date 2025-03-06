#!/usr/bin/env python3

"""
Zotero PDF Cleaner Tool

This script removes PDF attachments from Zotero items that were added before a specified date.
It's useful for cleaning up storage by removing PDFs from older items while keeping the metadata.

Usage examples:
- Remove PDFs from items added before February 1, 2025:
  python clean_zotero_pdfs.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --date 2025-02-01

- Do a dry run first to see what would be deleted:
  python clean_zotero_pdfs.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --date 2025-02-01 --dry_run

- Apply to a specific collection only:
  python clean_zotero_pdfs.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --date 2025-02-01 --collection_id COLLECTION_ID

- Process items added within a date range:
  python clean_zotero_pdfs.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --date 2025-02-01 --date_from 2025-01-01

Note: Your API key must have write permissions for this operation.
"""

from pyzotero import zotero
import argparse
from datetime import datetime, timezone
import re
import time
import sys

def parse_zotero_date(date_str):
    """Parse Zotero date format to datetime object"""
    if not date_str:
        return None
    
    # Zotero uses format like: 2023-01-15T14:32:10Z
    try:
        # Handle timezone: if Z (UTC), replace with +00:00
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        
        # Parse the date string to a datetime object
        dt = datetime.fromisoformat(date_str)
        
        # Ensure the datetime is timezone-aware
        # If it's naive (no timezone), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            
        return dt
        
    except ValueError as e:
        print(f"Warning: Could not parse date: {date_str} - {e}")
        return None

def get_all_items(zot, collection_id=None, date_from=None, limit=100):
    """Get all items from a Zotero library or collection, handling pagination"""
    start = 0
    all_items = []
    
    while True:
        try:
            if collection_id:
                items = zot.collection_items_top(collection_id, start=start, limit=limit)
            else:
                items = zot.items(start=start, limit=limit, itemType="-attachment")
            
            if not items:
                break
                
            all_items.extend(items)
            start += len(items)
            
            print(f"Retrieved {len(all_items)} items so far...")
            
            # Respect API rate limits
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error retrieving items: {e}")
            break
    
    # Filter by date_from if provided
    if date_from:
        filtered_items = []
        for item in all_items:
            date_added_str = item.get('data', {}).get('dateAdded', '')
            if date_added_str:
                date_added = parse_zotero_date(date_added_str)
                if date_added and date_added >= date_from:
                    filtered_items.append(item)
        
        print(f"Filtered from {len(all_items)} to {len(filtered_items)} items added since {date_from.strftime('%Y-%m-%d')}")
        return filtered_items
    
    return all_items

def check_item_pdf_status(zot, item_key, before_date, dry_run=False):
    """Check if an item has PDFs and if they fall within the grace period"""
    try:
        children = zot.children(item_key)
        pdf_count = 0
        old_pdf_count = 0
        
        for child in children:
            child_data = child.get('data', {})
            
            # Skip if not an attachment
            if child_data.get('itemType') != 'attachment':
                continue
            
            # Check if it's a PDF attachment
            is_pdf = (child_data.get('filename', '').lower().endswith('.pdf') or
                      child_data.get('contentType') == 'application/pdf')
            
            if is_pdf:
                pdf_count += 1
                # Check if it was added before the cut-off date
                date_str = child_data.get('dateAdded')
                date_added = parse_zotero_date(date_str)
                
                if date_added and date_added < before_date:
                    old_pdf_count += 1
                    
        return pdf_count, old_pdf_count
    except Exception as e:
        print(f"  Error checking PDF status for item {item_key}: {e}")
        return 0, 0

def remove_pdf_attachments(zot, item_key, before_date, dry_run=False):
    """Remove PDF attachments from an item if they were added before the cut-off date"""
    try:
        children = zot.children(item_key)
        removed_count = 0
        
        for child in children:
            child_data = child.get('data', {})
            
            # Skip if not an attachment
            if child_data.get('itemType') != 'attachment':
                continue
            
            # Check if it's a PDF attachment
            is_pdf = (child_data.get('filename', '').lower().endswith('.pdf') or
                      child_data.get('contentType') == 'application/pdf')
            
            if is_pdf:
                # Check if it was added before the cut-off date
                date_str = child_data.get('dateAdded')
                date_added = parse_zotero_date(date_str)
                
                if date_added and date_added < before_date:
                    child_key = child_data.get('key')
                    if child_key:
                        attachment_info = f"{child_data.get('title', 'Untitled')} " \
                                         f"(Added: {child_data.get('dateAdded')}, " \
                                         f"Filename: {child_data.get('filename', 'No filename')})"
                        
                        if dry_run:
                            print(f"  Would remove PDF attachment: {attachment_info}")
                            removed_count += 1
                        else:
                            try:
                                print(f"  Removing PDF attachment: {attachment_info}")
                                zot.delete_item(child)
                                print(f"  Successfully deleted attachment with key: {child_key}")
                                removed_count += 1
                                # Respect API rate limits
                                time.sleep(0.3)
                            except Exception as delete_error:
                                print(f"  Error when deleting attachment: {delete_error}")
        
        return removed_count
    except Exception as e:
        print(f"  Error processing attachments for item {item_key}: {e}")
        return 0

def main():
    parser = argparse.ArgumentParser(description="Remove PDF attachments from Zotero items added before a specified date")
    parser.add_argument("--zotero_api_key", required=True, help="Zotero API key with write permissions")
    parser.add_argument("--zotero_library_id", required=True, help="Zotero library ID")
    parser.add_argument("--library_type", default="group", choices=["group", "user"], 
                      help="Library type: 'group' or 'user' (default: 'group')")
    parser.add_argument("--collection_id", help="Optional collection ID to filter by")
    parser.add_argument("--date", required=True, help="Cut-off date in YYYY-MM-DD format")
    parser.add_argument("--date_from", help="Optional start date in YYYY-MM-DD format to filter items (only process items added on or after this date)")
    parser.add_argument("--dry_run", action="store_true", 
                      help="Don't actually delete anything, just show what would be deleted")
    args = parser.parse_args()

    # Parse the cut-off date
    try:
        # Make sure the cutoff date is timezone-aware (UTC)
        cut_off_date = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)
        print(f"Cut-off date: {cut_off_date.strftime('%Y-%m-%d')} (UTC)")
        
        # Parse the from date if provided
        date_from = None
        if args.date_from:
            date_from = datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
            print(f"Only processing items added on or after: {date_from.strftime('%Y-%m-%d')} (UTC)")
    except ValueError:
        print(f"Error: Invalid date format. Please use YYYY-MM-DD format.")
        sys.exit(1)

    # Initialize Zotero client
    zot = zotero.Zotero(args.zotero_library_id, args.library_type, args.zotero_api_key)
    
    # Fetch all items
    collection_info = f"collection {args.collection_id}" if args.collection_id else "entire library"
    date_filter = f" added on or after {args.date_from}" if args.date_from else ""
    print(f"Fetching items from {collection_info}{date_filter}...")
    
    all_items = get_all_items(zot, args.collection_id, date_from)
    print(f"Retrieved {len(all_items)} items total.")
    
    # Process and delete PDF attachments
    total_removed = 0
    items_with_removed = 0
    items_with_pdfs = 0
    items_with_grace_period_pdfs = 0
    
    mode = "DRY RUN - NO DELETIONS" if args.dry_run else "DELETING ATTACHMENTS"
    print(f"\n{mode} - Processing items for PDF attachments before {args.date}...\n")
    
    for i, item in enumerate(all_items, 1):
        data = item.get('data', {})
        item_key = data.get('key')
        title = data.get('title', 'No title')
        date_added = data.get('dateAdded', 'Unknown date')
        
        # Check if the item has PDFs and their status
        total_pdfs, old_pdfs = check_item_pdf_status(zot, item_key, cut_off_date, args.dry_run)
        
        # Determine PDF and grace period status for logging
        pdf_status = "has PDF" if total_pdfs > 0 else "no PDF"
        grace_status = "grace period" if total_pdfs > old_pdfs else "remove" if old_pdfs > 0 else "N/A"
        
        print(f"[{i}/{len(all_items)}] Processing: {title} [{pdf_status} | {grace_status}] (Added: {date_added}, Key: {item_key})")
        
        if total_pdfs > 0:
            items_with_pdfs += 1
            
            if total_pdfs > old_pdfs:
                items_with_grace_period_pdfs += 1
                if args.dry_run and old_pdfs > 0:
                    print(f"  Item has {total_pdfs} PDFs: {total_pdfs - old_pdfs} in grace period, {old_pdfs} eligible for removal")
            
            # Remove attachments for this item
            removed = remove_pdf_attachments(zot, item_key, cut_off_date, args.dry_run)
            
            if removed > 0:
                total_removed += removed
                items_with_removed += 1
        
        # Simple progress indicator
        if i % 10 == 0:
            print(f"Processed {i} of {len(all_items)} items ({i/len(all_items):.1%} complete)")
        
        # Respect API rate limits (especially important for large libraries)
        time.sleep(0.2)
    
    # Print summary
    action = "Would remove" if args.dry_run else "Removed"
    print(f"\nSummary:")
    print(f"Processed {len(all_items)} items")
    print(f"Items with PDFs: {items_with_pdfs}")
    print(f"Items with PDFs in grace period: {items_with_grace_period_pdfs}")
    print(f"{action} {total_removed} PDF attachments from {items_with_removed} items")
    
    if args.dry_run:
        print("\nThis was a dry run. No files were actually deleted.")
        print("To remove the files, run this script without the --dry_run flag.")

if __name__ == "__main__":
    main()