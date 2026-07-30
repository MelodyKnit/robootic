"""Reject filesystem absolute paths in Git submission candidates.

Run this script from the project root before staging or committing changes.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence, Tuple


BACKSLASH = chr(92)
WINDOWS_DRIVE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:" + "[" + re.escape(BACKSLASH) + r"/]"
)
UNC_PATH = re.compile(
    r"(?<!" + re.escape(BACKSLASH) + r")"
    + re.escape(BACKSLASH * 2)
    + r"[^" + re.escape(BACKSLASH) + r"/\r\n]+"
    + "[" + re.escape(BACKSLASH) + r"/]"
    + r"[^" + re.escape(BACKSLASH) + r"/\r\n]+"
)
FILE_URI = re.compile("file:" + "/" * 2, re.IGNORECASE)
POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_:])/"
    r"(?:home|root|usr|var|etc|opt|tmp|mnt|Users)(?:/|$)"
)
PATH_PATTERNS = (
    ("Windows drive path", WINDOWS_DRIVE_PATH),
    ("UNC path", UNC_PATH),
    ("file URI", FILE_URI),
    ("POSIX absolute path", POSIX_ABSOLUTE_PATH),
)


def find_absolute_path_forms(text: str) -> Tuple[Tuple[str, str], ...]:
    """Return filesystem absolute path forms found in one line of text."""

    findings = []
    for label, pattern in PATH_PATTERNS:
        findings.extend((label, match.group(0)) for match in pattern.finditer(text))
    return tuple(findings)


def submission_files() -> Tuple[Path, ...]:
    """Return tracked and non-ignored untracked files relative to the project root."""

    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    return tuple(Path(line) for line in result.stdout.splitlines() if line)


def submission_path_violations(files: Sequence[Path]) -> Tuple[str, ...]:
    """Return formatted violations for readable text files in the submission set."""

    violations: List[str] = []
    for path in files:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for label, value in find_absolute_path_forms(line):
                violations.append("{}:{}: {} detected: {}".format(path, line_number, label, value))
    return tuple(violations)


def main() -> int:
    """Run the Git submission path audit and return a process status code."""

    try:
        violations = submission_path_violations(submission_files())
    except subprocess.CalledProcessError as error:
        print("Unable to list Git submission candidates: {}".format(error), file=sys.stderr)
        return 2

    if violations:
        print("Filesystem absolute paths are not allowed in submission candidates:", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1

    print("Submission path check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
