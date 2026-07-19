# relay/main.py — HTTP Cloud Function: Drive watch notification -> repository_dispatch
#
# Push-to-pull relay. A Drive watch ping only means "something changed" — it carries no
# details. So on each ping we PULL the folder's current contents (files.list, the same query
# run.py uses) and diff against a set of "seen" file ids persisted in GCS. Every new file is
# batched into ONE repository_dispatch (burst-safe — no per-file dispatch storm). Seen-ids
# are advanced only after a successful dispatch, so a failed dispatch is retried next ping.
import json
import os

import functions_framework
import requests
from google.cloud import storage
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- config (non-secret; fine to hardcode) ---
REPO = "anden-karlsson/drive-ci-lab"          # owner/repo the dispatch targets
BUCKET = "drive-ci-test-relay-state"          # GCS bucket holding relay state
STATE_OBJECT = "seen_ids.json"                # object name inside that bucket
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# --- secrets/config injected as env vars at deploy time ---
#   CHANNEL_TOKEN     shared secret Google echoes in X-Goog-Channel-Token (anti-forgery)
#   DRIVE_PAT         GitHub PAT (Contents: write) for the dispatch
#   GDRIVE_SA_KEY     the service-account JSON key (same one run.py uses)
#   GDRIVE_FOLDER_ID  the drive-ci-inbox folder id


def _drive_client():
    key_info = json.loads(os.environ["GDRIVE_SA_KEY"])
    creds = service_account.Credentials.from_service_account_info(key_info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def _read_seen(bucket):
    blob = bucket.blob(STATE_OBJECT)
    return set(json.loads(blob.download_as_text())) if blob.exists() else None


def _write_seen(bucket, ids):
    bucket.blob(STATE_OBJECT).upload_from_string(json.dumps(sorted(ids)))


def _list_folder(drive, folder_id):
    """Return {file_id: name} for every non-trashed file in the folder (handles paging)."""
    out, page = {}, None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name)",
            pageToken=page,
        ).execute()
        for f in resp.get("files", []):
            out[f["id"]] = f["name"]
        page = resp.get("nextPageToken")
        if not page:
            return out


def _fire_dispatch(file_names):
    resp = requests.post(
        f"https://api.github.com/repos/{REPO}/dispatches",
        headers={
            "Authorization": f"Bearer {os.environ['DRIVE_PAT']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "drive-relay",
        },
        json={"event_type": "drive-upload", "client_payload": {"file_names": file_names}},
        timeout=10,
    )
    return resp.status_code


@functions_framework.http
def relay(request):
    # 1. reject forged pings (anyone can POST to a public URL)
    if request.headers.get("X-Goog-Channel-Token") != os.environ["CHANNEL_TOKEN"]:
        return ("forbidden", 403)
    # 2. the one-time "channel is live" ping — nothing actually changed
    if request.headers.get("X-Goog-Resource-State") == "sync":
        return ("sync ack", 200)

    bucket = storage.Client().bucket(BUCKET)
    drive = _drive_client()
    current = _list_folder(drive, os.environ["GDRIVE_FOLDER_ID"])  # {id: name}
    seen = _read_seen(bucket)

    # 3. first run ever: record the current folder as the baseline, process nothing
    if seen is None:
        _write_seen(bucket, set(current))
        return ("baseline recorded", 200)

    # 4. push-to-pull diff: which files are new since we last looked?
    new_ids = [fid for fid in current if fid not in seen]
    if not new_ids:
        return ("no new files", 200)
    names = sorted(current[fid] for fid in new_ids)

    # 5. ONE batched dispatch; advance 'seen' only if it succeeded (at-least-once)
    code = _fire_dispatch(names)
    if code == 204:
        _write_seen(bucket, seen | set(new_ids))
        return (f"dispatched {len(names)} file(s)", 200)
    print(f"dispatch failed ({code}); not advancing seen; files={names}")
    return ("dispatch failed, will retry", 200)  # still 2xx so Google keeps delivering