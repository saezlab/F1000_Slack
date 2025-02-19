import requests
import json
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime
import re
from tqdm import tqdm

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SciwheelAPIError(Exception):
    """Custom exception for Sciwheel API errors"""
    pass

# API Configuration
BASE_URL = "https://sciwheel.com/extapi/work"

# Field processing order for consistent RIS output
FIELD_ORDER = [
    'title',           # Title first
    'authors',         # Authors second
    'abstract',        # Abstract
    'journal',         # Journal info
    'volume',          # Volume
    'issue',           # Issue
    'pages',           # Pages
    'year',           # Publication year
    'keywords',        # Keywords
    'doi',            # DOI
    'pmid',           # PMID
    'arxivId',        # arXiv ID
    'publisher'        # Publisher
]

# Mapping between Sciwheel JSON fields and RIS tags
# Based on examples and RIS reference documentation
SCIWHEEL_TO_RIS_MAPPING = {
    # Publication Type
    'type': {
        'article': 'JOUR',  # Journal Article
        'preprint': 'GEN',  # Generic
        'book': 'BOOK',     # Book
        'chapter': 'CHAP',  # Book Chapter
        'conference': 'CONF', # Conference Proceeding
        'default': 'JOUR'   # Default to Journal Article
    },
    
    # Core fields
    'title': 'TI',         # Title
    'abstract': 'AB',      # Abstract
    'year': 'PY',         # Publication Year
    'month': None,        # Used with year for DA tag
    'day': None,          # Used with year/month for DA tag
    'doi': 'DO',          # DOI
    'url': 'UR',          # URL
    'pdfUrl': 'L1',       # PDF Link
    
    # Journal/Publication info
    'journal': {
        'name': ['JF', 'T2'],  # Full Journal Name and Secondary Title
        'abbreviation': 'JA',  # Journal Abbreviation
        'issn': 'SN',         # ISSN
        'eissn': 'SN'         # Electronic ISSN
    },
    'volume': 'VL',       # Volume
    'issue': 'IS',        # Issue
    'pages': ['SP', 'EP'], # Start Page, End Page (split on '-')
    'publisher': 'PB',     # Publisher
    
    # Identifiers
    'pmid': 'AN',         # Accession Number (format as "PMID:123456")
    'arxivId': 'AN',      # Accession Number (format as "arXiv:123456")
    
    # Authors and Contributors
    'authors': {
        'primary': 'AU',   # Authors (Last, First format)
        'secondary': 'A2'  # Secondary Authors
    },
    'editors': 'ED',      # Editors
    
    # Additional Metadata
    'keywords': 'KW',     # Keywords
    'language': 'LA',     # Language
    'notes': 'N1',        # Notes (with HTML formatting)
    
    # Date fields
    'accessed': 'Y2',     # Access Date (format as YYYY/MM/DD/HH:MM:SS)
    'dateAdded': 'DA',    # Date Added (format as YYYY/MM/DD/)
    
    # Optional/Special fields
    'shortTitle': 'ST',   # Short Title
    'series': 'T3',       # Series Title
    'isbn': 'SN',        # ISBN
    'database': 'DP'      # Database Provider
}

# Function to get RIS tag for a Sciwheel field
def get_ris_tag(field_name: str, field_value: Any = None, pub_type: str = None) -> Optional[str]:
    """
    Get the appropriate RIS tag for a Sciwheel field.
    
    Args:
        field_name: Name of the Sciwheel field
        field_value: Value of the field (used for type-specific mapping)
        pub_type: Publication type (if known)
    
    Returns:
        Corresponding RIS tag or None if no mapping exists
    """
    mapping = SCIWHEEL_TO_RIS_MAPPING.get(field_name)
    
    if mapping is None:
        return None
        
    if field_name == 'type':
        return f"TY  - {mapping.get(pub_type or 'default', 'JOUR')}"
    
    if isinstance(mapping, dict):
        # Handle nested mappings (like journal info)
        if isinstance(field_value, dict):
            return mapping.get(next(iter(field_value.keys())))
        return mapping.get('primary')  # Default to primary tag
        
    if isinstance(mapping, list):
        # For fields that map to multiple RIS tags
        return mapping[0]  # Return first by default
        
    return mapping

def get_project_items(project_id: str, headers: Dict[str, str], limit: int = None) -> List[Dict[str, Any]]:
    """
    Retrieve items from a Sciwheel project with pagination support.
    """
    items = []
    page = 1
    per_page = 50  # Default per_page value
    while True:
        # Updated URL to match the working example
        url = f'{BASE_URL}/references'
        params = {
            'projectId': project_id,
            'sort': 'addedDate:desc',
            'page': page,
            'size': min(per_page, limit) if limit else per_page
        }
        logging.info(f"Per_page: {params['size']}")
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            current_items = data.get('results', [])  # Changed from 'items' to 'results'
            if not current_items:
                break
                
            # If limit is specified, only take up to the limit
            if limit:
                remaining = limit - len(items)
                current_items = current_items[:remaining]
                
            items.extend(current_items)
            logging.info(f"Retrieved {len(current_items)} items from page {page}")
            
            # Break if we've reached the limit or the last page
            if limit and len(items) >= limit:
                logging.info(f"Limit reached, breaking")
                break
            if len(current_items) < params['size']:
                logging.info(f"Less items than asked, breaking")
                break
                
            page += 1
            logging.info(f"Moving to page {page}")
            
        except requests.exceptions.RequestException as e:
            raise SciwheelAPIError(f"Error retrieving items from project {project_id}: {str(e)}")
    
    return items

def get_item_notes(item_id: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Retrieve notes for a specific item.
    """
    # Updated URL structure to match working example
    url = f'{BASE_URL}/references/{item_id}/notes'
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()  # The response structure might be different
    except requests.exceptions.RequestException as e:
        logging.error(f"Error retrieving notes for item {item_id}: {str(e)}")
        return []

def format_note_content(note: Dict[str, Any]) -> str:
    """
    Format a note's content including both comments and highlighted text.
    Safely handles None values in notes.
    """
    parts = []
    
    # Add comment if present and not None
    comment = note.get('comment')
    if comment and isinstance(comment, str):
        comment = comment.strip()
        if comment:
            parts.append(f"Comment: {comment}")
    
    # Add highlighted text if present and not None
    highlight = note.get('highlightText')
    if highlight and isinstance(highlight, str):
        highlight = highlight.strip()
        if highlight:
            parts.append(f"Highlighted text: {highlight}")
    
    # Join with HTML line breaks for Zotero compatibility
    return "<p>" + "</p><p>".join(parts) + "</p>" if parts else ""

def format_ris_field(tag: str, value: str) -> str:
    """Format a single RIS field."""
    if not value:
        return ""
    # Handle multiline values (like abstracts)
    value = value.replace("\n", " ").strip()
    return f"{tag}  - {value}\n"  # Add back newline for each field

def transform_to_ris_format(sciwheel_items: List[Dict[str, Any]], headers: Dict[str, str]) -> str:
    """
    Transform Sciwheel items to RIS format using the defined mapping.
    Returns a string containing the full RIS content.
    """
    all_records = []
    
    # Initialize progress bar
    pbar = tqdm(sciwheel_items, desc="Converting to RIS format", unit="items")
    
    for item in pbar:
        record = []
        # Start with publication type
        pub_type = item.get('type', 'default')
        record.append(get_ris_tag('type', pub_type=pub_type))
        
        # Update progress bar description with current item title
        title = item.get('title', '')
        pbar.set_description(f"Processing: {title[:30]}..." if len(title) > 30 else f"Processing: {title}")
        
        # Process title and add short title if needed
        if title:
            title = re.sub(r'<i>(.*?)</i>', r'<i>\1</i>', title)  # Preserve italics
            record.append(format_ris_field("TI", title).rstrip())
            # Add short title if title is long
            if len(title) > 50:
                short_title = title[:47] + "..."
                record.append(format_ris_field("ST", short_title).rstrip())
        
        # Process authors from authorsText
        if authors_text := item.get('authorsText'):
            # Authors are already in correct format: "Kovaltsuk A, Leem J, ..."
            authors = [a.strip() for a in authors_text.split(", ")]
            for author in authors:
                if author:
                    # Names are already in correct order with space between last and first
                    name_parts = author.split()
                    if len(name_parts) > 1:
                        first = name_parts[-1]  # Last part is the first name/initial
                        last = ' '.join(name_parts[:-1])  # Everything else is the last name
                        record.append(format_ris_field("AU", f"{last}, {first}").rstrip())
                    else:
                        # Single word name, use as is
                        record.append(format_ris_field("AU", author).rstrip())
        
        # Add abstract
        if abstract := item.get('abstractText'):
            # Clean up the abstract text
            abstract = abstract.replace("<br>", " ").replace("<br/>", " ")
            abstract = re.sub(r'\s+', ' ', abstract)  # Replace multiple spaces with single space
            record.append(format_ris_field("AB", abstract).rstrip())
        
        # Process publication year (use publishedYear instead of year)
        if pub_year := item.get('publishedYear'):
            record.append(format_ris_field("PY", str(pub_year)).rstrip())
            # Also add date if available
            date_str = f"{pub_year}/"
            if month := item.get('month'):
                date_str = f"{pub_year}/{month:02d}/"
            if day := item.get('day'):
                date_str = f"{pub_year}/{month:02d}/{day:02d}/"
            record.append(format_ris_field("DA", date_str).rstrip())
        
        # Process journal info
        if journal := item.get('journal'):
            if isinstance(journal, dict):
                if journal_name := journal.get('name'):
                    record.append(format_ris_field("JF", journal_name).rstrip())
                    record.append(format_ris_field("T2", journal_name).rstrip())
                if abbrev := journal.get('abbreviation'):
                    record.append(format_ris_field("JA", abbrev).rstrip())
        
        # Volume and Issue
        if volume := item.get('volume'):
            record.append(format_ris_field("VL", volume).rstrip())
        if issue := item.get('issue'):
            record.append(format_ris_field("IS", issue).rstrip())
        
        # Process pagination (split into start and end pages)
        if pagination := item.get('pagination'):
            if '-' in pagination:
                start_page, end_page = pagination.split('-')
                record.append(format_ris_field("SP", start_page).rstrip())
                record.append(format_ris_field("EP", end_page).rstrip())
            else:
                # Single page
                record.append(format_ris_field("SP", pagination).rstrip())
        
        # Process identifiers (PMID, PMCID, DOI)
        if pmid := item.get('pubMedId'):
            record.append(format_ris_field("AN", f"PMID:{pmid}").rstrip())
        if pmcid := item.get('pmcId'):
            record.append(format_ris_field("AN", f"PMCID:{pmcid}").rstrip())
        if doi := item.get('doi'):
            record.append(format_ris_field("DO", doi).rstrip())
        
        # Keywords from f1000Tags
        if tags := item.get('f1000Tags'):
            for tag in tags:
                record.append(format_ris_field("KW", tag).rstrip())
        
        # URLs in priority order
        urls = {}
        database_provider = None
        
        # 1. DOI-based URL
        if doi:
            if "arxiv" in doi.lower():
                urls['doi'] = f"http://arxiv.org/abs/{doi.split('/')[-1]}"
                database_provider = "arXiv.org"
            elif "biorxiv" in doi.lower():
                urls['doi'] = f"http://biorxiv.org/lookup/doi/{doi}"
                database_provider = "bioRxiv"
            else:
                urls['doi'] = f"https://doi.org/{doi}"
                database_provider = "DOI.org (Crossref)"
            
            record.append(format_ris_field("UR", urls['doi']).rstrip())
        
        # 2. Publisher URL
        elif pub_url := item.get('publisherUrl'):
            record.append(format_ris_field("UR", pub_url).rstrip())
        
        # Add PDF URL as L1 if available
        if pdf_url := item.get('pdfUrl'):
            record.append(format_ris_field("L1", pdf_url).rstrip())
        
        # Add database provider if determined
        if database_provider:
            record.append(format_ris_field("DP", database_provider).rstrip())
        
        # Standard fields
        record.append(format_ris_field("LA", "en").rstrip())
        
        # Publisher name
        if publisher := item.get('publisher'):
            record.append(format_ris_field("PB", publisher).rstrip())
        
        # Access date
        access_date = datetime.now().strftime("%Y/%m/%d/%H:%M:%S")
        record.append(format_ris_field("Y2", access_date).rstrip())
        
        # Process notes
        notes = get_item_notes(item['id'], headers)
        if notes:
            note_contents = []
            for note in notes:
                note_content = format_note_content(note)
                if note_content.strip():
                    note_contents.append(note_content)
            
            if note_contents:
                combined_notes = "<div data-schema-version=\"9\">" + "".join(note_contents) + "</div>"
                record.append(format_ris_field("N1", combined_notes).rstrip())
        
        # End record
        record.append("ER  -")
        
        # Join record fields with single newline
        all_records.append("\n".join(record))
    
    # Join all records with single newline
    return "\n".join(all_records)

def main(api_key: str, project_id: str, limit: int = None, output_dir: str = ".", 
         file_prefix: str = "sciwheel", save_json: bool = False):
    """
    Main function to orchestrate the export process.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        # Create output directory first
        os.makedirs(output_dir, exist_ok=True)
        
        logging.info(f"Starting export for project {project_id}" + (f" (limit: {limit})" if limit else ""))
        
        # Get items from the project
        items = get_project_items(project_id, headers, limit)
        logging.info(f"Retrieved {len(items)} items total")
        
        # Create timestamp and suffix for filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        limit_suffix = f"_limit{limit}" if limit else ""
        
        # Save raw Sciwheel items to JSON if requested
        if save_json:
            json_file = os.path.join(output_dir, f'{file_prefix}_raw_{timestamp}{limit_suffix}.json')
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved raw Sciwheel data to '{json_file}'")
        
        # Transform items to RIS format
        ris_content = transform_to_ris_format(items, headers)
        
        # Save RIS file
        output_file = os.path.join(output_dir, f'{file_prefix}_export_{timestamp}{limit_suffix}.ris')
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(ris_content)
        
        logging.info(f"Export complete. File saved as '{output_file}'")
        
    except SciwheelAPIError as e:
        logging.error(f"API Error: {str(e)}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    import os
    import sys
    import argparse
    
    # Set up command line argument parsing
    parser = argparse.ArgumentParser(description='Export publications from Sciwheel to Zotero format')
    parser.add_argument('--project-id', default='419191', help='Sciwheel project ID')
    parser.add_argument('--limit', type=int, help='Maximum number of publications to process (for testing)')
    parser.add_argument('--output-dir', default='.', help='Directory to save output files')
    parser.add_argument('--prefix', default='sciwheel', help='Prefix for output filenames')
    parser.add_argument('--save-json', action='store_true', help='Save raw JSON data (for debugging)')
    args = parser.parse_args()
    
    # Get API key from environment variable
    api_key = os.getenv('SCIWHEEL_API_KEY')
    if not api_key:
        print("Error: SCIWHEEL_API_KEY environment variable not set")
        sys.exit(1)
    
    main(api_key, args.project_id, args.limit, args.output_dir, args.prefix, args.save_json)
