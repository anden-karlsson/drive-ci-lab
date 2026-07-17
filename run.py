"""Dummy pipeline for the Drive-event-driven CI lab. Grows a flag per phase."""
import argparse
import io
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--hello", action="store_true", help="phase 1: prove CI runs this file")
parser.add_argument("--download", metavar="FILE_NAME",
                    help="phase 3: download FILE_NAME from the shared Drive folder")
args = parser.parse_args()

if args.hello:
    print("hello from run.py - executed by CI")

if args.download:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    key_info = json.loads(os.environ["GDRIVE_SA_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        key_info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds)

    folder_id = os.environ["GDRIVE_FOLDER_ID"]
    query = (f"name = '{args.download}' "
             f"and '{folder_id}' in parents and trashed = false")
    resp = drive.files().list(q=query, fields="files(id, name, size)").execute()
    files = resp.get("files", [])
    if not files:
        sys.exit(f"ERROR: no file named {args.download!r} in folder {folder_id}")

    file = files[0]
    request = drive.files().get_media(fileId=file["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    out_path = f"downloaded_{file['name']}"
    with open(out_path, "wb") as f:
        f.write(buf.getvalue())
    print(f"downloaded {file['name']} ({file.get('size', '?')} bytes) -> {out_path}")