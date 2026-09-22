"""Mirror Git references without exposing private repository metadata in logs."""

import base64
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


class SyncError(Exception):
    """A public-safe error that intentionally omits Git command output."""


def run_git(arguments, environment, failure_message):
    try:
        result = subprocess.run(
            ["git", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=600,
        )
    except (subprocess.SubprocessError, OSError):
        raise SyncError(failure_message) from None
    return result.stdout


def parse_references(output):
    return {
        reference: revision
        for revision, reference in (line.split("\t", 1) for line in output.splitlines())
    }


def read_remote_references(repository_url, environment, failure_message):
    return parse_references(
        run_git(
            ["ls-remote", "--refs", repository_url, "refs/heads/*", "refs/tags/*"],
            environment,
            failure_message,
        )
    )


def references_match(source_references, target_references):
    # Extra destination references belong to its users and are never deleted.
    return all(
        target_references.get(reference) == revision
        for reference, revision in source_references.items()
    )


def is_ancestor(repository, ancestor, descendant, environment):
    """True when `ancestor` is already part of `descendant`'s history (a plain fast-forward)."""
    try:
        result = subprocess.run(
            ["git", "-C", repository, "merge-base", "--is-ancestor", ancestor, descendant],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (subprocess.SubprocessError, OSError):
        raise SyncError("Ancestry check failed.") from None
    # Exit 0: ancestor, exit 1: not an ancestor, anything else: missing object or Git failure.
    if result.returncode not in (0, 1):
        raise SyncError("Ancestry check failed.")
    return result.returncode == 0


def preserve_divergent_references(
    source_directory, target_url, source_references, target_references, environment
):
    """Back up destination commits that the source no longer contains before overwriting them.

    A destination branch/tag whose commit is not part of the source history means one of two
    things: the source was rewound (reset + force push) or something pushed to the destination
    directly. The destination commit is kept as a timestamped `mirror-backup/...` branch so the
    forced push below never discards work; the destination-only backup branches are never synced.
    """
    divergent = [
        reference
        for reference, revision in source_references.items()
        if reference in target_references and target_references[reference] != revision
    ]
    if not divergent:
        return False
    # Fetch the destination's current tips into a private namespace so their objects are local.
    # --no-tags is essential: auto-followed destination tags would land in refs/tags and then be
    # pushed back as if they came from the source.
    fetched = {reference: "refs/mirror-target/" + reference[len("refs/") :] for reference in divergent}
    run_git(
        [
            "-C",
            source_directory,
            "fetch",
            "--no-tags",
            "--",
            target_url,
            *(f"+{reference}:{local}" for reference, local in fetched.items()),
        ],
        environment,
        "Destination fetch failed.",
    )
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    backups = []
    for reference, local in fetched.items():
        # Peel annotated tags so the backup branch always points at a commit.
        target_commit = run_git(
            ["-C", source_directory, "rev-parse", "--verify", local + "^{commit}"],
            environment,
            "Destination commit lookup failed.",
        ).strip()
        if is_ancestor(source_directory, target_commit, source_references[reference], environment):
            continue  # Plain fast-forward: nothing on the destination would be lost.
        # refs/heads/x -> refs/heads/mirror-backup/heads/x/<stamp>; refs/tags/t -> .../tags/t/<stamp>
        backups.append(f"{target_commit}:refs/heads/mirror-backup/{reference[len('refs/') :]}/{stamp}")
    if not backups:
        return False
    # Backup names are new, so this is a normal (non-forced) push of destination-only branches.
    run_git(
        ["-C", source_directory, "push", "--", target_url, *backups],
        environment,
        "Backup push failed; nothing was overwritten.",
    )
    return True


def sync_references(
    source_url, target_url, source_environment, target_environment, working_directory
):
    source_references = read_remote_references(
        source_url, source_environment, "Source reference check failed."
    )
    target_references = read_remote_references(
        target_url, target_environment, "Destination reference check failed."
    )
    if not source_references:
        raise SyncError("The source has no branches or tags; synchronization stopped.")
    if references_match(source_references, target_references):
        return

    source_directory = str(working_directory / "source.git")
    run_git(
        ["clone", "--bare", "--", source_url, source_directory],
        source_environment,
        "Source fetch failed.",
    )
    source_references = parse_references(
        run_git(
            [
                "-C",
                source_directory,
                "for-each-ref",
                "--format=%(objectname)%09%(refname)",
                "refs/heads",
                "refs/tags",
            ],
            source_environment,
            "Fetched reference check failed.",
        )
    )
    preserved = preserve_divergent_references(
        source_directory, target_url, source_references, target_references, target_environment
    )
    # The source is authoritative for every branch/tag name it has, so its references are forced
    # onto the destination (following rewinds and replaced tags). No mirror/prune: references that
    # exist only on the destination, including mirror-backup branches, are never deleted.
    run_git(
        [
            "-C",
            source_directory,
            "push",
            "--",
            target_url,
            "+refs/heads/*:refs/heads/*",
            "+refs/tags/*:refs/tags/*",
        ],
        target_environment,
        "Push failed. Check destination access privately.",
    )
    target_references = read_remote_references(
        target_url, target_environment, "Destination verification failed."
    )
    if not references_match(source_references, target_references):
        raise SyncError("Reference verification failed; the success timestamp was not updated.")
    if preserved:
        print("Divergent destination commits were preserved as mirror-backup branches before overwrite.")


def synchronize():
    secret_names = (
        "SOURCE_REPO_URL",
        "SOURCE_USERNAME",
        "SOURCE_TOKEN",
        "TARGET_REPO_URL",
        "TARGET_SSH_KEY",
    )
    if any(not os.environ.get(secret_name) for secret_name in secret_names):
        raise SyncError("Required repository secrets are missing.")
    source_url = os.environ["SOURCE_REPO_URL"]
    target_url = os.environ["TARGET_REPO_URL"]
    source_address = urlsplit(source_url)
    if source_address.scheme != "https" or not source_address.hostname or source_address.username:
        raise SyncError("The source must be an HTTPS Git URL without embedded credentials.")
    if not target_url.startswith("git@github.com:"):
        raise SyncError("The destination must be a GitHub SSH Git URL.")

    # Git subprocesses receive only the credentials needed for their own endpoint.
    environment = {name: value for name, value in os.environ.items() if name not in secret_names}
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "",
        }
    )
    source_environment = environment.copy()
    credentials = os.environ["SOURCE_USERNAME"] + ":" + os.environ["SOURCE_TOKEN"]
    authorization = base64.b64encode(credentials.encode()).decode()
    print("::add-mask::" + authorization, flush=True)
    source_environment.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_1": f"http.https://{source_address.netloc}/.extraheader",
            "GIT_CONFIG_VALUE_1": "Authorization: Basic " + authorization,
        }
    )

    with tempfile.TemporaryDirectory(prefix="private-mirror-") as temporary_directory:
        working_directory = Path(temporary_directory)
        private_key = working_directory / "deploy-key"
        with private_key.open("w", encoding="utf-8", newline="\n") as key_file:
            key_file.write(os.environ["TARGET_SSH_KEY"].replace("\r", "").rstrip("\n") + "\n")
        private_key.chmod(0o600)
        known_hosts = working_directory / "known-hosts"
        known_hosts.write_text(
            "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n",
            encoding="ascii",
        )
        target_environment = environment.copy()
        target_environment["GIT_SSH_COMMAND"] = shlex.join(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-i",
                str(private_key),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=30",
                "-o",
                "UserKnownHostsFile=" + str(known_hosts),
            ]
        )
        sync_references(
            source_url, target_url, source_environment, target_environment, working_directory
        )


def main():
    try:
        synchronize()
        synchronized_at = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S %z"
        )
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output_file:
            output_file.write("synchronized_at=" + synchronized_at + "\n")
    except SyncError as error:
        print("::error::" + str(error))
        return 1
    except Exception:  # noqa: BLE001 - last-resort privacy boundary, never report success.
        # Tracebacks can contain private URLs, branch names or credential material.
        print(
            "::error::Synchronization failed unexpectedly; private diagnostic output was suppressed."
        )
        return 1
    print("All source references verified. Destination-only references untouched; no deletions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
