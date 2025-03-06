#!/usr/bin/env python3

"""
Zotero Debug and Maintenance Tool

This script provides various tools for debugging and maintaining a Zotero library:

1. Query and display the latest items from a Zotero library or collection
2. Search for specific papers by title
3. Examine child items (notes and attachments) for papers
4. Remove PDF attachments from specific items

Usage examples:
- List last 50 items: 
  python debug_zotero.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID
  
- Search for a paper by title: 
  python debug_zotero.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --title "Paper Title"
  
- Show child items and attachments: 
  python debug_zotero.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --show_children
  
- Remove PDF attachments from an item: 
  python debug_zotero.py --zotero_api_key YOUR_API_KEY --zotero_library_id YOUR_LIBRARY_ID --remove_pdf ITEM_KEY

Note: For write operations like removing attachments, your API key must have write permissions.
"""

from pyzotero import zotero
import argparse
from datetime import datetime
import json
import re
import pprint

def clean_note(note_text):
    """Clean HTML tags from note text"""
    return re.sub(r'<[^>]+>', '', note_text)

def format_item(item):
    """Format a Zotero item for display"""
    data = item.get('data', {})
    meta = item.get('meta', {})
    return {
        'title': data.get('title', 'No title'),
        'dateAdded': data.get('dateAdded', 'No date'),
        'dateModified': data.get('dateModified', 'No date'),
        'itemType': data.get('itemType', 'No type'),
        'key': data.get('key', 'No key'),
        'creators': data.get('creators', []),
        'collections': data.get('collections', []),
        'createdByUser': meta.get('createdByUser', {}),
        'lastModifiedByUser': meta.get('lastModifiedByUser', {})
    }

def get_item_children_detailed(zot, item_key):
    """Get detailed information about all children of an item"""
    try:
        children = zot.children(item_key)
        child_items = []
        
        for child in children:
            item_type = child['data'].get('itemType', '')
            child_info = {
                'key': child['data'].get('key', ''),
                'itemType': item_type,
                'dateAdded': child['data'].get('dateAdded', ''),
                'dateModified': child['data'].get('dateModified', ''),
                'createdByUser': child.get('meta', {}).get('createdByUser', {}),
                'lastModifiedByUser': child.get('meta', {}).get('lastModifiedByUser', {})
            }
            
            # Add type-specific information
            if item_type == 'note':
                child_info['note'] = clean_note(child['data'].get('note', ''))
            elif item_type == 'attachment':
                child_info['title'] = child['data'].get('title', 'No title')
                child_info['linkMode'] = child['data'].get('linkMode', 'Unknown')
                child_info['contentType'] = child['data'].get('contentType', 'Unknown')
                child_info['filename'] = child['data'].get('filename', 'No filename')
                child_info['url'] = child['data'].get('url', '')
            
            child_items.append(child_info)
            
        return child_items
    except Exception as e:
        print(f"Error getting children items: {e}")
        return []

def get_item_notes_detailed(zot, item_key):
    """Get detailed notes attached to an item"""
    try:
        children = zot.children(item_key)
        notes = []
        for child in children:
            if child['data'].get('itemType') == 'note':
                note_info = {
                    'note': clean_note(child['data'].get('note', '')),
                    'key': child['data'].get('key', ''),
                    'dateAdded': child['data'].get('dateAdded', ''),
                    'dateModified': child['data'].get('dateModified', ''),
                    'createdByUser': child.get('meta', {}).get('createdByUser', {}),
                    'lastModifiedByUser': child.get('meta', {}).get('lastModifiedByUser', {})
                }
                notes.append(note_info)
        return notes
    except Exception as e:
        print(f"Error getting notes: {e}")
        return []

def remove_pdf_attachments(zot, item_key):
    """Remove PDF attachments from an item"""
    try:
        children = zot.children(item_key)
        removed_count = 0
        
        for child in children:
            child_data = child.get('data', {})
            
            # Check if it's an attachment and has a PDF filename or content type is PDF
            if (child_data.get('itemType') == 'attachment' and 
                (child_data.get('filename', '').lower().endswith('.pdf') or
                 child_data.get('contentType') == 'application/pdf')):
                
                child_key = child_data.get('key')
                if child_key:
                    print(f"Removing PDF attachment: {child_data.get('title', 'Untitled')} "
                          f"(Filename: {child_data.get('filename', 'No filename')})")
                    
                    # Try to delete the item
                    try:
                        # Pass the entire child object to delete_item, not just the key
                        result = zot.delete_item(child)
                        print(f"Successfully deleted attachment with key: {child_key}")
                        removed_count += 1
                    except Exception as delete_error:
                        print(f"Error when deleting attachment: {delete_error}")
        
        return removed_count
    except Exception as e:
        print(f"Error removing PDF attachments: {e}")
        return 0

def main():
    parser = argparse.ArgumentParser(description="Debug Zotero items")
    parser.add_argument("--zotero_api_key", required=True, help="Zotero API key")
    parser.add_argument("--zotero_library_id", required=True, help="Zotero library ID")
    parser.add_argument("--collection_id", help="Optional collection ID to filter by")
    parser.add_argument("--title", help="Search for a specific paper title")
    parser.add_argument("--show_children", action="store_true", help="Show all child items including attachments")
    parser.add_argument("--remove_pdf", help="Remove PDF attachments from item with specified key")
    args = parser.parse_args()

    # Initialize Zotero client
    zot = zotero.Zotero(args.zotero_library_id, 'group', args.zotero_api_key)
    
    try:
        # If removing PDF attachments
        if args.remove_pdf:
            item_key = args.remove_pdf
            print(f"\nLooking for PDF attachments for item {item_key}...")
            
            # Verify the item exists
            try:
                item = zot.item(item_key)
                print(f"Found item: {item['data'].get('title', 'No title')}")
                
                # Remove PDF attachments
                count = remove_pdf_attachments(zot, item_key)
                print(f"Removed {count} PDF attachment(s).")
                
                if count > 0:
                    print("\nRemaining children for this item:")
                    children = get_item_children_detailed(zot, item_key)
                    if children:
                        for i, child in enumerate(children, 1):
                            print(f"\n  Child {i} ({child['itemType']}):")
                            if child['itemType'] == 'note':
                                print(f"    Content: {child['note'][:100]}..." if len(child['note']) > 100 else f"    Content: {child['note']}")
                            elif child['itemType'] == 'attachment':
                                print(f"    Title: {child.get('title', 'No title')}")
                                print(f"    Link mode: {child.get('linkMode', 'Unknown')}")
                                print(f"    Content type: {child.get('contentType', 'Unknown')}")
                                print(f"    Filename: {child.get('filename', 'None')}")
                    else:
                        print("No children remaining.")
                
                return
            except Exception as e:
                print(f"Error finding item {item_key}: {e}")
                return

        # Fetch items
        if args.collection_id:
            items = zot.collection_items_top(args.collection_id, limit=50, sort='dateAdded', direction='desc')
        else:
            items = zot.items(limit=50, sort='dateAdded', direction='desc')

        # If searching for specific title
        if args.title:
            title_lower = args.title.lower()
            for item in items:
                current_title = item.get('data', {}).get('title', '')
                if current_title.lower() == title_lower:
                    formatted = format_item(item)
                    print("\nPaper Details:")
                    print(f"Title: {formatted['title']}")
                    print(f"Type: {formatted['itemType']}")
                    print(f"Key: {formatted['key']}")
                    print(f"Date Added: {formatted['dateAdded']}")
                    print(f"Date Modified: {formatted['dateModified']}")
                    print(f"Created by: {formatted['createdByUser'].get('username', 'Unknown')}")
                    print(f"Last modified by: {formatted['lastModifiedByUser'].get('username', 'Unknown')}")
                    
                    if args.show_children:
                        print("\nChild Items (Notes, Attachments, etc.):")
                        children = get_item_children_detailed(zot, item['key'])
                        if children:
                            for i, child in enumerate(children, 1):
                                print(f"\nChild {i} ({child['itemType']}):")
                                if child['itemType'] == 'note':
                                    print(f"Content: {child['note']}")
                                elif child['itemType'] == 'attachment':
                                    print(f"Title: {child.get('title', 'No title')}")
                                    print(f"Link mode: {child.get('linkMode', 'Unknown')}")
                                    print(f"Content type: {child.get('contentType', 'Unknown')}")
                                    print(f"Filename: {child.get('filename', 'None')}")
                                    print(f"URL: {child.get('url', 'None')}")
                                print(f"Key: {child['key']}")
                                print(f"Date Added: {child['dateAdded']}")
                                print(f"Date Modified: {child['dateModified']}")
                                print(f"Created by: {child['createdByUser'].get('username', 'Unknown')}")
                                print(f"Last modified by: {child['lastModifiedByUser'].get('username', 'Unknown')}")
                        else:
                            print("No child items found for this paper.")
                    else:
                        print("\nDetailed Notes Information:")
                        notes = get_item_notes_detailed(zot, item['key'])
                        if notes:
                            for i, note in enumerate(notes, 1):
                                print(f"\nNote {i}:")
                                print(f"Content: {note['note']}")
                                print(f"Key: {note['key']}")
                                print(f"Date Added: {note['dateAdded']}")
                                print(f"Date Modified: {note['dateModified']}")
                                print(f"Created by: {note['createdByUser'].get('username', 'Unknown')}")
                                print(f"Last modified by: {note['lastModifiedByUser'].get('username', 'Unknown')}")
                        else:
                            print("No notes found for this paper.")
                    
                    print("\nRaw item data for debugging:")
                    pprint.pprint(item)
                    return
            print("\nPaper not found with exact title match.")
            return

        # If no specific title provided, show all items
        print("\nFetching last 50 items:\n")
        for item in items:
            formatted = format_item(item)
            print(f"Title: {formatted['title']}")
            print(f"Type: {formatted['itemType']}")
            print(f"Key: {formatted['key']}")
            print(f"Date Added: {formatted['dateAdded']}")
            print(f"Date Modified: {formatted['dateModified']}")
            if formatted['creators']:
                print("Creators:")
                for creator in formatted['creators']:
                    if 'firstName' in creator and 'lastName' in creator:
                        print(f"  - {creator['firstName']} {creator['lastName']}")
                    else:
                        print(f"  - {creator.get('name', 'Unknown')}")
            print("Collections:", formatted['collections'])
            
            # Show children information for each item
            children = get_item_children_detailed(zot, item['data']['key'])
            if children:
                print(f"\nChild items ({len(children)}):")
                
                # Count types of children
                child_types = {}
                for child in children:
                    item_type = child['itemType']
                    if item_type not in child_types:
                        child_types[item_type] = 0
                    child_types[item_type] += 1
                
                # Print summary of child types
                for child_type, count in child_types.items():
                    print(f"  - {child_type}: {count}")
                
                # Print details of each child if requested
                if args.show_children:
                    for i, child in enumerate(children, 1):
                        print(f"\n  Child {i} ({child['itemType']}):")
                        if child['itemType'] == 'note':
                            print(f"    Content: {child['note'][:100]}..." if len(child['note']) > 100 else f"    Content: {child['note']}")
                        elif child['itemType'] == 'attachment':
                            print(f"    Title: {child.get('title', 'No title')}")
                            print(f"    Link mode: {child.get('linkMode', 'Unknown')}")
                            print(f"    Content type: {child.get('contentType', 'Unknown')}")
                            print(f"    Filename: {child.get('filename', 'None')}")
            else:
                print("No child items")
            
            print("-" * 80 + "\n")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()