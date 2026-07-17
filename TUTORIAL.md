# drive-ci-lab — tutorial handover

Throwaway repo for **learning event-driven CI by doing**: uploading a file to Google Drive
must **immediately** (true push, no polling cron) trigger a GitHub Actions workflow that
**downloads that file** onto the runner and processes it with a dummy `run.py`.

**`STUDY.md` is the lab manual the user follows on their own** — full walkthroughs for
all phases (steps, commands broken down, code, checkpoints), plus the gotchas log. Chat is
for when they get stuck. Keep both files updated: status here; walkthrough corrections,
⚠ VERIFY resolutions, and new gotchas in `STUDY.md`.

## Interaction contract (important for the assisting agent)

- **Tutor mode, do NOT one-shot.** The agent provides copy-pasteable code blocks + Bash
  commands and explains what each does; the **user runs everything themselves** (they're
  learning). Proceed to the next step only after the user confirms the phase checkpoint.
- User works in **WSL2 Ubuntu** (`~/Projects/drive-ci-lab`), stock apt tool versions
  (e.g. `gh` 2.45 — its `run view --log` is silently broken; use `--json` fields or
  `gh api .../logs` + `unzip -p` instead). Adapt commands to the installed tooling; do not
  propose tool upgrades/detours. Authoring files: prefer an editor (`code <file>`).
- **Break down every command** part by part: what each flag does, why the modern idiom
  looks that way, plus by-hand examples of what one-liners automate.
- `gh` CLI installed and logged in as `anden-karlsson`.
- User knowledge level: knows push-triggered CI basics (has a lint+test workflow in another
  repo); everything else here is new — explain, don't just instruct.

## The plan (phases; ✅ = checkpoint passed)

1. ✅ **Baseline CI** — repo + `run.py --hello` stub + `.github/workflows/ci.yml` (`on: push`).
   Checkpoint: green run in Actions tab.
2. ✅ **`repository_dispatch` + curl** — new `drive.yml` with
   `on: repository_dispatch: types: [drive-upload]` + `concurrency: {group: drive-run, cancel-in-progress: false}`;
   print `client_payload`. User creates a fine-grained PAT (repo-scoped) and fires the
   workflow with a manual `curl` POST to `/repos/anden-karlsson/drive-ci-lab/dispatches`.
   Checkpoint: payload visible in run logs.
3. **Service account + download from Drive** — GCP project, enable Drive API, service
   account + JSON key, share the Drive test folder with the SA email, repo secret
   `GDRIVE_SA_KEY`; extend `run.py` (google-api-python-client + google-auth) to download the
   file named in the payload (or newest in folder). Checkpoint: curl-fired run downloads a
   manually uploaded file. **← WE ARE HERE**
4. **Relay A: Pipedream (managed)** — Drive "New Files (Instant)" trigger (true push;
   Pipedream owns watch-channel renewal) → HTTP step POSTing repository_dispatch with the
   PAT, file id/name in `client_payload`. Checkpoint (milestone 1): upload → run downloads
   that file within seconds; test multi-file burst + concurrency queueing.
5. **Relay B: self-hosted Cloud Function (the internals)** — pause Pipedream;
   `relay/main.py` (HTTP function: validate `X-Goog-Channel-Token`, ignore `sync`,
   push-to-pull via changes/files query, debounce, POST dispatch) deployed with `gcloud`;
   `relay/register_watch.py` registers the Drive watch channel; `renew.yml` (weekly cron)
   re-registers it — cron for the *subscription* only, detection stays push. Checkpoint
   (milestone 2): upload → function log → run downloads file.
6. **Wrap-up** — recap both architectures; teardown checklist (watch channel, function, SA
   key, PAT, Pipedream workflow, repo) so no orphaned credentials remain.

Verification is checkpoint-based (observable event chain), no test suite. Facts not certain
from memory (Drive watch-channel max TTL, exact Pipedream trigger name, PAT permission
minimums) must be verified against docs at that phase, not asserted.

## Current status (2026-07-16)

- Phases 1–2 complete. `drive.yml` on `main` uses the safe `env:` indirection for payload
  (script-injection lesson covered); concurrency group is `${{ github.ref }}` (user's
  informed choice — equivalent to a single lane for dispatch events).
- Fine-grained PAT `drive-ci-lab-dispatch` created (repo-scoped, Contents: read/write,
  expires 2026-08-15); stored locally in `.env.txt` (gitignored via `.env*`), loaded with
  `source .env.txt` — env vars are per-shell, a 401 taught that.
- Both curl dispatches verified in run logs (run 29538180334: `event action: drive-upload`
  + payload). Log-fetch idiom for gh 2.45: `gh api .../runs/<id>/logs > /tmp/logs.zip &&
  unzip -p /tmp/logs.zip | grep -A4 "event action:"`.
- Gotchas already hit and fixed: heredoc wrote `ci.yml` to repo root (mv); `checkout @v4`
  typo (sed); edited workflow not pushed before dispatch (runner uses `main`, not disk);
  `.env.txt` nearly committable → `.gitignore` + name-files-explicitly-in-`git add` habit.
- **Next action:** Phase 3 — GCP project, enable Drive API, service account + JSON key,
  share Drive test folder with SA email, repo secret `GDRIVE_SA_KEY`, extend `run.py` to
  download the payload-named file.

This file is part of the lab: update the status section as phases complete; delete the repo
(and this file with it) at teardown.
