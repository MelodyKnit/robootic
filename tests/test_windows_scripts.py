"""Black-box coverage for the supported Windows batch entry points."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


# Project test entry points run from the repository root, matching the batch contract.
PROJECT_ROOT = Path(".")


class WindowsScriptTests(unittest.TestCase):
    """Verify wrapper safety without invoking Poetry, pnpm, or device code."""

    def _run_with_fake_executable(self, script_name, executable_name, executable_body, arguments):
        """Run one batch file with a narrow fake tool placed first on PATH."""

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / executable_name
            executable.write_text(executable_body, encoding="ascii")
            environment = os.environ.copy()
            environment["PATH"] = directory + os.pathsep + environment.get("PATH", "")
            return subprocess.run(
                ["cmd.exe", "/d", "/c", "scripts\\" + script_name] + list(arguments),
                cwd=str(PROJECT_ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )

    def _run_with_fake_poetry(self, script_name, arguments):
        """Run a Python wrapper while preventing the real Poetry executable from starting."""

        return self._run_with_fake_executable(
            script_name,
            "poetry.bat",
            "@echo off\n"
            "if /I \"%~1\"==\"--version\" (\n"
            "  echo Poetry version 1.8.5\n"
            "  exit /b 0\n"
            ")\n"
            "echo [FAKE-POETRY] %*\n"
            "exit /b 0\n",
            arguments,
        )

    def test_run_requires_a_command_and_explicit_web_configuration(self):
        """No wrapper default may silently start the web service or choose localstore."""

        no_command = self._run_with_fake_poetry("run.bat", ())
        dev_without_command = self._run_with_fake_poetry("run.bat", ("--dev",))

        self.assertEqual(2, no_command.returncode, no_command.stdout)
        self.assertEqual(2, dev_without_command.returncode, dev_without_command.stdout)
        self.assertNotIn("[FAKE-POETRY] run python", no_command.stdout)
        self.assertNotIn("[FAKE-POETRY] run python", dev_without_command.stdout)

    def test_run_forwards_explicit_web_arguments_and_only_applies_dev_to_web(self):
        """The wrapper validates web config while leaving supported CLI arguments intact."""

        web = self._run_with_fake_poetry(
            "run.bat",
            ("web", "--config-file", "configs/development.json", "--port", "8011"),
        )
        dev_web = self._run_with_fake_poetry(
            "run.bat",
            ("--dev", "web", "--config-file", "configs/development.json"),
        )
        invalid_dev = self._run_with_fake_poetry("run.bat", ("--dev", "gpu-check"))

        self.assertEqual(0, web.returncode, web.stdout)
        self.assertIn(
            "[FAKE-POETRY] run python -m gripper_ai_controller web --config-file configs/development.json --port 8011",
            web.stdout,
        )
        self.assertEqual(0, dev_web.returncode, dev_web.stdout)
        self.assertIn("--reload", dev_web.stdout)
        self.assertEqual(2, invalid_dev.returncode, invalid_dev.stdout)
        self.assertNotIn("[FAKE-POETRY] run python", invalid_dev.stdout)

    def test_calibration_wrapper_allows_only_registered_calibration_commands(self):
        """Unsupported commands and unconfigured capture cannot reach the application CLI."""

        unsupported = self._run_with_fake_poetry("calibration.bat", ("web",))
        unconfigured_capture = self._run_with_fake_poetry(
            "calibration.bat", ("calibration-capture-charuco", "--frame-count", "25"),
        )
        offline = self._run_with_fake_poetry(
            "calibration.bat",
            (
                "calibration-generate-charuco",
                "--output-file",
                "temp/gripper-ai-controller/charuco/board.png",
            ),
        )

        self.assertEqual(2, unsupported.returncode, unsupported.stdout)
        self.assertEqual(2, unconfigured_capture.returncode, unconfigured_capture.stdout)
        self.assertNotIn("[FAKE-POETRY] run python", unsupported.stdout)
        self.assertNotIn("[FAKE-POETRY] run python", unconfigured_capture.stdout)
        self.assertEqual(0, offline.returncode, offline.stdout)
        self.assertIn("calibration-generate-charuco", offline.stdout)

    def test_frontend_test_action_forwards_every_playwright_argument(self):
        """The frontend wrapper must not discard flags after the first test selector."""

        completed = self._run_with_fake_executable(
            "frontend.bat",
            "pnpm.bat",
            "@echo off\n"
            "echo [FAKE-PNPM] %*\n"
            "exit /b 0\n",
            (
                "test",
                "tests/object-pose.spec.ts",
                "--project=chromium",
                "--grep=object-pose",
            ),
        )

        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("[FAKE-PNPM] exec playwright test tests/object-pose.spec.ts", completed.stdout)
        self.assertIn("--project chromium", completed.stdout)
        self.assertIn("--grep object-pose", completed.stdout)


if __name__ == "__main__":
    unittest.main()
