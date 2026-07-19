# relay/register_watch.py — subscribe the relay to Drive changes (changes.watch).
#
# Run this AFTER deploying the function (you need its public URL). It tells Drive:
# "POST a notification to FUNCTION_URL whenever anything in the SA's view changes."
# The function then pulls the folder and dispatches. Channels expire (max 1 week for the
# changes resource), so renew.yml re-runs this weekly. We deliberately DON'T stop old
# channels: an expiring one dies on its own, and any brief overlap is harmless because
# main.py dedupes by file id (a duplicate ping just yields "no new files").
import json
import os
import time
import uuid

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
# changes.watch max TTL = 604800s (1 week); default 3600s. Request just under the max.
TTL_SECONDS = 7 * 24 * 3600 - 300


def main():
    function_url = os.environ["FUNCTION_URL"]     # printed by `gcloud functions deploy`
    channel_token = os.environ["CHANNEL_TOKEN"]   # MUST match the function's CHANNEL_TOKEN
    key_info = json.loads(os.environ["GDRIVE_SA_KEY"])

    creds = service_account.Credentials.from_service_account_info(key_info, scopes=SCOPES)
    drive = build("drive", "v3", credentials=creds)

    # anchor point in the changes feed to watch from ("everything after now")
    start_token = drive.changes().getStartPageToken().execute()["startPageToken"]

    expiration_ms = int((time.time() + TTL_SECONDS) * 1000)   # absolute epoch, in millis
    channel = {
        "id": str(uuid.uuid4()),      # unique id for THIS subscription
        "type": "web_hook",           # HTTP push notifications
        "address": function_url,      # where Google POSTs the pings
        "token": channel_token,       # echoed back as X-Goog-Channel-Token (anti-forgery)
        "expiration": expiration_ms,
    }

    resp = drive.changes().watch(pageToken=start_token, body=channel).execute()
    print("watch channel registered:")
    print(f"  id         = {resp.get('id')}")
    print(f"  resourceId = {resp.get('resourceId')}")
    granted = int(resp.get("expiration", expiration_ms)) / 1000
    print(f"  expires    = {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(granted))}")


if __name__ == "__main__":
    main()