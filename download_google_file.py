# 
# This script downloads a file from Google Drive to the local machine using a service account for authentication.
# 
# Arguments:
#   1. --file_id: The ID of the Google Drive file to download.
#   2. --output_path: The local path where the file will be saved.
#   3. --service_account_file: (Optional) Path to the service account JSON file. Defaults to './service_account.json'.
#
# Example Usage:
#   python download_google_file.py --file_id <FILE_ID> --output_path <OUTPUT_PATH> [--service_account_file <SERVICE_ACCOUNT_FILE>]
# 
# by: Attila Gabor

import argparse
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import os

def download_file(file_id, output_path, service_account_file):
    """Download a file from Google Drive."""
    if not os.path.exists(service_account_file):
        raise FileNotFoundError(f"Service account file not found: {service_account_file}")

    # Authenticate using the service account file
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=['https://www.googleapis.com/auth/drive']
    )

    # Build the Drive service
    service = build('drive', 'v3', credentials=credentials)

    # Request the file from Google Drive
    request = service.files().get_media(fileId=file_id)

    # Download the file to the specified output path
    with io.FileIO(output_path, 'wb') as output:
        downloader = MediaIoBaseDownload(output, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Download progress: {int(status.progress() * 100)}%")

    print(f"File downloaded successfully to {output_path}.")

def validate_arguments(args):
    """Basic validation for user inputs."""
    if not args.file_id:
        raise ValueError("--file_id is required.")
    if not args.output_path:
        raise ValueError("--output_path is required.")
    if args.service_account_file and not os.path.exists(args.service_account_file):
        raise FileNotFoundError(f"Service account file not found: {args.service_account_file}")

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Download a file from Google Drive.")
    parser.add_argument("--file_id", required=True, help="The ID of the Google Drive file to download.")
    parser.add_argument("--output_path", required=True, help="The local path where the file will be saved.")
    parser.add_argument(
        "--service_account_file", 
        default="./service_account.json", 
        help="Path to the service account JSON file (default: ./service_account.json)."
    )
    
    args = parser.parse_args()

    # Validate arguments
    try:
        validate_arguments(args)
    except Exception as e:
        print(f"Error: {e}")
        return

    # Download the file
    try:
        download_file(args.file_id, args.output_path, args.service_account_file)
    except Exception as e:
        print(f"Failed to download file: {e}")

if __name__ == "__main__":
    main()
