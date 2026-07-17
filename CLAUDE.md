# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A throwaway lab for **learning event-driven CI by doing**: an upload to Google Drive must trigger (true push, no polling) a GitHub Actions `repository_dispatch` workflow that downloads the file and processes it with `run.py`. There is no build system, no dependencies, and no test suite — verification is checkpoint-based (observe the event chain end-to-end: upload → relay → Actions run → file downloaded).

**Read `TUTORIAL.md` first.** It is the source of truth for the phase plan, current status, and next action. Keep its "Current status" section updated as phases complete. `STUDY.md` is the user's persistent study document (explanations, command breakdowns, by-hand examples, gotchas log) — append each phase's teaching material there as it completes, so the conversation doesn't need to carry it.

## Interaction contract (do not skip)

- **Tutor mode — do NOT one-shot the work.** Provide copy-pasteable code blocks and Bash commands with explanations; the user runs everything themselves. Advance to the next phase only after the user confirms the phase checkpoint (e.g., a green run in the Actions tab).
- The user knows push-triggered CI basics; everything else here (repository_dispatch, PATs, service accounts, Drive watch channels, Pipedream, Cloud Functions) is new — explain, don't just instruct.
- User works in Git Bash on Windows; prefer editor-based file authoring (`code <file>`) over heredocs for the user's own edits.
- `gh` CLI is installed and logged in as `anden-karlsson`.
- Facts not certain from memory (Drive watch-channel max TTL, exact Pipedream trigger names, minimal PAT permissions) must be verified against current docs at that phase, not asserted.

## Structure

- `run.py` — dummy pipeline; gains one CLI flag per phase (currently only `--hello`). Later phases add Drive-download logic (google-api-python-client + google-auth).
- `.github/workflows/ci.yml` — phase 1 baseline (`on: push`, runs `run.py --hello`).
- Later phases add `.github/workflows/drive.yml` (`on: repository_dispatch: types: [drive-upload]`, with a `drive-run` concurrency group), `relay/` (self-hosted Cloud Function relay + watch registration), and `renew.yml` (weekly cron that re-registers the Drive watch channel — the cron is for the subscription only; detection stays push-based).

## Teardown

At wrap-up, walk the teardown checklist (watch channel, Cloud Function, SA key, PAT, Pipedream workflow, repo) so no orphaned credentials remain.
