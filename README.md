# Repository mirror synchronization

Generic one-way Git repository synchronization. This repository contains only
automation code and its operational status, never mirrored application code.

## Status

Last successful synchronization: 2026-09-24 11:30:16 +0800

The timestamp is recorded after every successful synchronization and reference
verification, including runs with no source changes. Failed synchronizations do
not update it. Status commits modify only this controller repository's README,
never the source repository or destination application's README.

## Schedule

- Monday-Friday, UTC+8.
- Every 30 minutes from 09:00 through 12:00, inclusive.
- Every 30 minutes from 14:00 through 19:00, inclusive.
- 18 scheduled runs per weekday; no scheduled runs during lunch or on weekends.
- Manual runs: **Actions > Synchronize repositories > Run workflow**.

GitHub schedules are best-effort. Runs may be delayed or dropped during load;
this is not a guaranteed thirty-minute replication SLA. Public scheduled workflows
can be disabled after 60 days without repository activity. Successful status
commits record real operational activity, but prolonged failures still require
monitoring and manual attention.

## Secrets

Configure these Actions repository secrets; do not put values in files or issues:

- `SOURCE_REPO_URL`: source HTTPS Git URL without embedded credentials.
- `SOURCE_USERNAME`: source Git authentication username.
- `SOURCE_TOKEN`: source repository read credential.
- `TARGET_REPO_URL`: destination SSH URL in `git@github.com:...` format.
- `TARGET_SSH_KEY`: write-enabled deploy key for that destination only.

The automatically issued `GITHUB_TOKEN` updates this repository's README in a
separate job. It is not the credential used to write to the mirrored repository.

## Behavior and privacy

- Compare branch/tag revisions first; fetch full history only when they differ.
- Transfer original commits, branch names, and tags without rewriting history.
- The source is authoritative: every branch/tag name it has is forced onto the
  destination, so source rewinds and replaced tags are followed instead of failing.
- Never delete references or push to the source.
- Preserve destination-only branches, such as independent agent work.
- Before overwriting a destination branch/tag whose commit is not in the source
  history (source rewind, or a direct push to the destination), that commit is kept
  on the destination as `mirror-backup/<heads|tags>/<name>/<timestamp>`. Backup
  branches are destination-only, never synced, and must be deleted manually.
- Direct pushes to the destination on a branch name the source also has are
  overwritten on the next run; bring such work back through the source instead.
- Do not execute code fetched from the source repository.
- Do not publish source URLs, usernames, branch names, commit IDs, reference
  counts, raw Git output, private code, artifacts, or caches in this controller.
- Expose only generic success/failure messages and successful synchronization time.
- Keep diagnostic investigation private; masking alone is not the privacy boundary.
- Pin third-party actions and the destination host's published SSH key.
- Only scheduled and manually authorized runs are supported; no PR-triggered jobs.
- Git LFS transfer is not implemented. Use with non-LFS repositories only.

Public standard GitHub-hosted runners have free execution minutes under the
current GitHub policy, but concurrency, job duration, storage, and acceptable-use
limits still apply. This workflow does not use larger paid runners.
