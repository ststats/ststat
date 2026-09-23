# ststat - Part 1 starter

This is the safe foundation for the central data pipeline.

Part 1 does **not** move any StarUniv or Synergy collector yet. It only creates the common Supabase connection, job logging, ownership rules, and a manual GitHub Actions healthcheck.

## Architecture in Part 1

- `ststat` conceptually owns the data domain.
- The existing Cloudflare Worker keeps running every 2 minutes for realtime broadcast updates.
- An external cron may trigger the GitHub Actions workflow every 4 hours for batch work.
- Batch jobs are forbidden from overwriting realtime broadcast fields.
- StarUniv admin continues to own manually managed content such as calendar/history/external tools.

## 1. Create the repository

Create a new GitHub repository named `ststat` and copy the contents of this folder into it.

## 2. Run the Supabase migration once

Open Supabase -> SQL Editor -> New query.

Copy all of:

`migrations/001_pipeline_base.sql`

and click Run.

This creates only `public.sync_jobs`.

It does not modify members, matches, calendar, videos, or realtime broadcast tables.

## 3. Add GitHub Actions settings

Open the `ststat` GitHub repository:

Settings -> Secrets and variables -> Actions

Create a Repository Variable:

- Name: `SUPABASE_URL`
- Value: the same Supabase Project URL used by StarUniv

Create a Repository Secret:

- Name: `SUPABASE_SERVICE_ROLE_KEY`
- Value: the server-side Supabase service role key

Never put the service role key in browser JavaScript, Git files, README, or `.env` committed to Git.

## 4. Push the files

Example:

```cmd
git add .
git commit -m "Initialize ststat pipeline foundation"
git push origin main
```

## 5. Run the first test

GitHub -> Actions -> `Run ststat pipeline` -> Run workflow

If successful, Supabase Table Editor -> `sync_jobs` should contain a row like:

- `job_name`: `healthcheck`
- `status`: `success`

## 6. Existing Cloudflare Worker

Do not remove it.

The worker continues to run every 2 minutes and update realtime broadcast information.

In the target architecture this worker is considered part of the `ststat` data system, even if its code remains deployed separately on Cloudflare.

Later we can either:

1. keep the Worker in its current separate deployment, or
2. move the Worker source code into the `ststat` repository while still deploying it to Cloudflare.

Part 1 does not change the Worker.

## 7. External 4-hour cron

Part 1 deliberately has no GitHub cron schedule.

The workflow is `workflow_dispatch` only so your external cron can trigger it every 4 hours.

We will add actual batch jobs in later parts.

## Next: Part 2

Part 2 will centralize roster / `tier_members` handling and new-player candidate detection without moving EloBoard statistics yet.
