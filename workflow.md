# SwimProgression — production workflow

How to keep the app running and the data accurate. Keep this file open the first few times you do each step; after that it should be obvious.

## Roles

- **Coach** — manages the team's roster; views swimmers, times, progress.
- **Admin** — uploads meet PDFs, fixes data issues, maintains the whole system.

---

## 1. Set up the roster (Coach)

1. Open the **Roster** tab.
2. Add swimmers one at a time, or use the batch import.
3. Required: first name, last name, gender.
4. Optional: ct_id (CT Swim member ID), birth year, training group, parent email, notes.

If a swimmer's ct_id is known, the Admin "Refresh from CT Swim" step in §2 will pull their full career history.

## 2. Fetch swimmer times from CT Swim (Admin)

1. Open the **Admin** tab → **Refresh from CT Swim**.
2. The system queries ctswim.org for each roster swimmer and stores meets + times.
3. Use when: a swimmer is brand new, or their meets aren't in your imported PDFs yet, or they swam out-of-state.

## 3. Import meet PDFs (Admin)

After every CT meet (weekly during season):

1. Download the Hy-Tek result PDF from ctswim.org (or from the meet host).
2. Open the **Data** tab.
3. Click **Upload PDF**, or drag the file onto the upload bar.
4. Review the preview:
   - Detected meet name and date
   - Number of swims and unique swimmers
   - Roster matches
   - Any lines that couldn't be read (you can fix these later in §4)
5. Click **Confirm import**.

Notes:

- Duplicate PDFs are blocked by default. Tick **Re-import even if duplicate** to override.
- Championship meets often have multiple PDFs (Senior + Age Group + Distance + Relay). Upload each one — they append to the same meet automatically.
- Importing the same meet from a different host (e.g. a corrected PDF) appends new rows by `(name, event, time)` rather than overwriting. To force a full replace, wipe the meet's data first.

## 4. Fix data issues (Admin)

On the **Data** tab, every row has ✎ (edit) and × (delete) buttons:

- **✎ Edit** — opens a form with the current row values. Common fixes: a misparsed first or last name, age or gender wrong, stroke or course mislabeled, time with a stray character.
- **× Delete** — removes one swim record. Two-step confirm.
- **+ Add row** (top of tab) — manually add a swim the import missed. Pick the meet, fill in the swimmer + event + time, save.

Use the filter row + column dropdowns to narrow down rows before editing in bulk.

## 5. Roster updates

- **New swimmer joining**: add them in Roster. If they already appear in any imported meet, their age and gender fill in automatically.
- **Swimmer leaving**: remove from Roster. Their swim history remains in the Data tab (other teams' rankings may still need it).

## 6. Periodic maintenance (Admin)

| Frequency | Task |
|---|---|
| Weekly during season | Import new meet PDFs as they're posted. |
| Monthly | Sort the Data tab by Date desc and scan for obvious errors; spot-fix with the edit buttons. |
| Pre-championship | Re-run **Refresh from CT Swim** for swimmers expecting new times that week. |
| Off-season | Nothing required — historical data stays. |

## 7. Wiping data (Admin, rare)

**Data tab → Wipe all data** (red button, two-step confirm).

Use when:

- Resetting after a major data issue.
- Starting fresh after a new import engine version.

What it removes:

- Imported meets and their swim records.
- Observed age and gender values on roster swimmers.

What it preserves:

- The roster itself (names, ct_id, parent emails, training groups, notes).
- Optionally: PDFs you uploaded manually (tick **Keep my uploaded PDFs**).

## 8. Deployment update flow (Admin / Engineer)

1. Code changes are pushed to GitHub `main`.
2. Render auto-deploys from `main`.
3. Static assets (`/static/app.js`, `/static/style.css`) carry an mtime cache buster — users get the new version on the next page load.
4. The DB schema runs through `init_db()` at startup. It's idempotent — new columns added with `ALTER TABLE` are no-ops if already present.

## 9. Seed prod from local data (one-time)

If you have a local checkout with all the historical meet PDFs already parsed (e.g. the first time you bring the app live, or after a major parser update), ship the parsed data to production in one shot instead of uploading hundreds of PDFs one at a time through the UI.

### What you need locally

- The PDFs you want to seed, in `pdfs_local/`.
- A working local Python environment.

### Step 1 — Build the seed locally

```bash
python3 scripts/build_seed_db.py --force
```

This writes three files into `seed/`:

| File | Size | Use |
|---|---|---|
| `swim_seed.sqlite` | ~110 MB | standalone sqlite file (inspect with `sqlite3`) |
| `swim_seed.sql` | ~120 MB | replayable SQL (use this in prod) |
| `swim_seed.sql.gz` | ~8 MB | gzipped — easier to upload |

The seed contains **only** the three swim-data tables (`meet_pdf_cache`, `meet_pdf_swimmers`, `meet_pdf_results`). It does **not** touch any roster table.

### Step 2 — Wipe prod's existing swim data (keep roster)

Yes, do this — otherwise prod's earlier partial PDF imports may clash with the seed.

1. Log in to prod as admin.
2. Open the **Data** tab → click the red wipe button at the top right (labelled **Wipe parsed data** on prod today, **Wipe all data** on post-deploy local).
3. In the modal, **leave the "Keep manually-uploaded PDFs" checkbox unchecked** — you want a completely empty slate.
4. Click the red confirm button (**Yes, wipe data**).

Both UI variants hit the same endpoint (`POST /api/admin/reset_age_data` with `keep_uploaded=false, skip_autofill=true`), which runs `db.reset_pdf_caches()` (DELETEs all rows from `meet_pdf_cache`, `meet_pdf_swimmers`, `meet_pdf_results`) and `db.reset_member_triangulation()` (clears `birth_year`, `birth_month`, `age_observed`, `age_observed_at`, `age_synced_at`, `age_window_days` on every `team_members` row). The roster identity columns (`first_name`, `last_name`, `gender`, `ct_id`, `parent_email`, `training_group`, `notes`) are not touched.

### Step 3 — Push the seed onto the prod disk

Two ways — pick whichever fits your Render plan.

**Option A — Render Shell tab** (Starter plan and above — confirmed available per `DEPLOY_RENDER.md`):

1. Upload `seed/swim_seed.sql.gz` somewhere the prod box can reach via `curl` (a private GitHub release, an unlisted Gist, an S3 presigned URL, a Dropbox share link, or a temporary `python3 -m http.server` exposed via ngrok). The content is just publicly-posted CT meet results, but use a private channel anyway.
2. Render dashboard → your service → **Shell** tab.
3. Confirm the DB path first:
   ```bash
   ls -la /var/data/swimprogression.db
   ```
   The startup log line (`Persist root: /var/data | DB: /var/data/swimprogression.db | …`) is the authoritative source. If your deploy mounts the disk elsewhere, substitute that path.
4. Pull and apply:
   ```bash
   curl -L -o /tmp/swim_seed.sql.gz "<YOUR_URL>"
   gunzip /tmp/swim_seed.sql.gz
   wc -l /tmp/swim_seed.sql                                # sanity: ~474k lines
   sqlite3 /var/data/swimprogression.db < /tmp/swim_seed.sql
   ```
5. Verify counts in the shell:
   ```bash
   sqlite3 /var/data/swimprogression.db <<'EOF'
   SELECT 'cache', COUNT(*) FROM meet_pdf_cache;
   SELECT 'swimmers', COUNT(*) FROM meet_pdf_swimmers;
   SELECT 'results', COUNT(*) FROM meet_pdf_results;
   SELECT 'roster (unchanged)', COUNT(*) FROM team_members;
   EOF
   ```
   Expected: `cache|348`, `swimmers|82496`, `results|391340`, plus your roster count unchanged.
6. Clean up: `rm /tmp/swim_seed.sql`

**Option B — One-shot deploy job** (any plan):

1. Commit `seed/swim_seed.sql.gz` to the repo (or attach to a GitHub release).
2. Add a guarded line to your release/start command, for example:
   ```bash
   [ -n "$SEED_ME" ] && gunzip -c seed/swim_seed.sql.gz | sqlite3 "$DB_PATH"
   ```
3. Set `SEED_ME=1` in Render env vars, redeploy, watch logs.
4. After it succeeds, **remove `SEED_ME`** so the next deploy doesn't re-seed.

### Step 4 — Confirm

1. Hard-refresh the prod **Data** tab.
2. You should see ~348 meets and ~391k swims in the summary cards. (Prod is at parser v8 today, the seeded rows are stamped v13. Prod's `upload_pdf` treats `parser_version >= ctp.PARSER_VERSION` as already-current, so seeded rows read as up-to-date and prod won't try to re-parse them.)
3. Pick a roster swimmer who attended any of the seeded meets — their age and gender should auto-fill on the next page load (the lookup in `db.lookup_member_observations` reads from `meet_pdf_swimmers`).

### One subtle property of the seed

Every seeded `meet_pdf_cache` row has `pdf_url = 'seed:<filename>.pdf'` (non-empty). Prod's "Keep manually-uploaded PDFs" wipe mode keeps any cache row whose `pdf_url` is non-NULL/non-empty, so a future wipe with that checkbox CHECKED will preserve the seeded data while clearing auto-discovered cache rows. Useful when you re-seed later or refresh auto-discovery alone.

### Cost on Render

Seeding adds about ~110 MB of swim data to the persistent disk.

- **Tier**: no upgrade needed — this is a shell op, not an HTTP request, so no request-timeout limit applies.
- **Disk**: grows ~110 MB. Render persistent disk is $0.25/GB/month, so this adds ~$0.03/month.
- **Bandwidth**: ~8 MB of one-time download into the prod box. Negligible.

## 10. Backups

- The SQLite database is on the Render disk; the path is defined in `paths.py`.
- Production: enable Render disk snapshots, or schedule a `sqlite3 .backup` cron and ship to S3.
- Roster + imported swim data are fully recoverable from a backup.
- Uploaded PDF files themselves are **not** stored — only their content (extracted into rows). Keep originals in cloud storage if you need to re-import after a wipe.

---

## Daily reference — the simple loop

```
Coach fills roster
   ↓
Admin refreshes CT Swim times once
   ↓
After each meet, Admin uploads the PDF
   ↓
Admin spot-fixes any bad rows with edit / delete / add row
   ↓
Swimmers and coaches see updated times on their dashboards
```

That's it. Most of the time, the only weekly task is **upload the new meet PDF**.
