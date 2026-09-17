"""Record a verified synchronization time in the public controller README only."""

import base64
import json
import os
import re
import subprocess
from datetime import datetime


def update_status():
    synchronized_at = os.environ["SYNCHRONIZED_AT"]
    datetime.strptime(synchronized_at, "%Y-%m-%d %H:%M:%S %z")
    endpoint = "repos/" + os.environ["GITHUB_REPOSITORY"] + "/contents/README.md"
    response = subprocess.run(
        ["gh", "api", endpoint + "?ref=main"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    current_file = json.loads(response.stdout)
    readme = base64.b64decode(current_file["content"]).decode("utf-8")
    updated_readme, replacements = re.subn(
        r"(?m)^Last successful synchronization: .+$",
        "Last successful synchronization: " + synchronized_at,
        readme,
    )
    if replacements != 1:
        raise ValueError("Expected exactly one status line.")
    if updated_readme == readme:
        return
    # The SHA check prevents a concurrent manual README edit from being overwritten.
    payload = {
        "message": '[chore]"Sync status": "Record successful synchronization"',
        "branch": "main",
        "sha": current_file["sha"],
        "content": base64.b64encode(updated_readme.encode("utf-8")).decode("ascii"),
    }
    subprocess.run(
        ["gh", "api", "--method", "PUT", endpoint, "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )


def main():
    try:
        update_status()
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        print("::error::Synchronization succeeded, but recording its timestamp failed.")
        return 1
    print("Last successful synchronization recorded in the controller README.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
