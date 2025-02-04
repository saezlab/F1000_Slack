#!/usr/bin/env python3
import argparse
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_file(file_id, input_path, service_account_file):
    """
    Upload (update) a file on Google Drive using a service account.
    
    This function updates the file with ID `file_id` using the local file at `input_path`.
    It uses a resumable upload to report progress.
    """
    if not os.path.exists(service_account_file):
        raise FileNotFoundError(f"Service account file not found: {service_account_file}")
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    # Authenticate using the service account file.
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=['https://www.googleapis.com/auth/drive']
    )
    service = build('drive', 'v3', credentials=credentials)
    
    # Prepare the file upload. Adjust the mimetype if your file is not CSV.
    media = MediaFileUpload(input_path, mimetype='text/csv', resumable=True)
    
    # Request to update the existing file on Drive.
    request = service.files().update(fileId=file_id, media_body=media)
    
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    print(f"File uploaded successfully from '{input_path}' to file ID '{file_id}'.")

def validate_arguments(args):
    """Basic validation for user inputs."""
    if not args.file_id:
        raise ValueError("--file_id is required.")
    if not args.input_path:
        raise ValueError("--input_path is required.")
    if args.service_account_file and not os.path.exists(args.service_account_file):
        raise FileNotFoundError(f"Service account file not found: {args.service_account_file}")

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Upload (update) a file on Google Drive using a service account.")
    parser.add_argument("--file_id", required=True, help="The ID of the Google Drive file to update.")
    parser.add_argument("--input_path", required=True, help="The local path of the file to upload.")
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

    # Upload the file
    try:
        upload_file(args.file_id, args.input_path, args.service_account_file)
    except Exception as e:
        print(f"Failed to upload file: {e}")

if __name__ == "__main__":
    main()
