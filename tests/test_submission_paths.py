"""Tests for the repository submission-path audit."""

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path("scripts")))

from check_submission_paths import find_absolute_path_forms, submission_files, submission_path_violations


class SubmissionPathTests(unittest.TestCase):
    """Verify that the submission-path audit detects filesystem paths precisely."""

    def test_detects_supported_absolute_path_forms(self):
        separator = chr(92)
        text = "\n".join(
            (
                "drive=C:" + separator + "workspace",
                "network=" + separator * 2 + "server" + separator + "share",
                "uri=file:" + "/" * 2 + "server/path",
                "posix=/" + "home" + "/developer",
            )
        )

        findings = []
        for line in text.splitlines():
            findings.extend(label for label, _ in find_absolute_path_forms(line))

        self.assertEqual(
            {"Windows drive path", "UNC path", "file URI", "POSIX absolute path"}, set(findings)
        )

    def test_allows_urls_and_simulation_object_paths(self):
        text = "https://example.invalid/model simulation://frame-1 /UR3/joint1 configs/development.json"

        self.assertEqual((), find_absolute_path_forms(text))

    def test_current_submission_candidates_have_no_absolute_paths(self):
        self.assertEqual((), submission_path_violations(submission_files()))


if __name__ == "__main__":
    unittest.main()
