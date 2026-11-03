"""Tests ci/continuous-heads against local upstream repositories and a local apt repository.

Needs git, gpg, python3-yaml and dpkg-dev. Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs)


class ContinuousHeadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # A scratch openocean-build: its git history gives the packaging revision
        self.root = self.tmp / "build"
        (self.root / "ci").mkdir(parents=True)
        for script in ("continuous-heads", "project-config", "verify-signature"):
            shutil.copy2(ROOT / "ci" / script, self.root / "ci" / script)
        shutil.copy2(ROOT / "defaults.yaml", self.root / "defaults.yaml")
        self.apt = self.tmp / "apt"

        gnupghome = self.tmp / "signer-gnupg"
        gnupghome.mkdir(mode=0o700)
        self.env = dict(os.environ, GNUPGHOME=str(gnupghome), GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.org",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.org")
        self.env.pop("GITHUB_STEP_SUMMARY", None)
        run("gpg", "--batch", "--passphrase", "", "--quick-gen-key", "maint@example.org", "ed25519", "sign", "never",
            env=self.env)
        out = run("gpg", "--batch", "--with-colons", "--list-secret-keys", env=self.env).stdout
        self.maint = re.search(r"^fpr:+([0-9A-F]+):", out, re.M).group(1)
        self.maint_key = run("gpg", "--batch", "--armor", "--export", self.maint, env=self.env).stdout

    def upstream(self, name, signed):
        repo = self.tmp / f"upstream-{name}"
        run("git", "init", "-q", "-b", "main", repo, env=self.env)
        (repo / "README").write_text(name)
        run("git", "-C", repo, "add", "README", env=self.env)
        run("git", "-C", repo, "commit", "-q", *(["-S" + self.maint] if signed else []), "-m", "one", env=self.env)
        return repo

    def project(self, name, upstream, enabled=True, push_dispatch=False):
        d = self.root / "projects" / name
        (d / "debian").mkdir(parents=True)
        (d / "keys").mkdir()
        (d / "project.yaml").write_text(textwrap.dedent(f"""\
            apt_url: file://{self.apt}
            upstream:
              git: file://{upstream}
            source: git
            continuous:
              enabled: {str(enabled).lower()}
              push_dispatch: {str(push_dispatch).lower()}
            """))
        (d / "maintainers.yaml").write_text(f"maintainers:\n  - name: Maint\n    gpg: [{self.maint}]\n")
        (d / "keys" / "maint.asc").write_text(self.maint_key)
        (d / "debian" / "changelog").write_text(
            f"{name} (1.0-1) trixie; urgency=medium\n\n  * Test.\n\n -- T <t@example.org>  Wed, 28 Oct 2026 00:00:00 +0000\n")

    def commit_build(self):
        run("git", "init", "-q", self.root, env=self.env)
        run("git", "-C", self.root, "add", ".", env=self.env)
        run("git", "-C", self.root, "commit", "-q", "-m", "projects", env=self.env)

    def heads(self, *args):
        result = subprocess.run([str(self.root / "ci" / "continuous-heads"), *args],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), result.stderr

    def test_signed_head_is_built(self):
        repo = self.upstream("hello", signed=True)
        self.project("hello", repo)
        self.commit_build()
        sha = run("git", "-C", repo, "rev-parse", "HEAD", env=self.env).stdout.strip()
        builds, log = self.heads()
        self.assertEqual(builds, [{"project": "hello", "sha": sha, "version": f"0+git1.g{sha[:7]}-0oos1"}])
        self.assertIn("signed by Maint", log)

    def test_unsigned_head_is_skipped(self):
        self.project("hello", self.upstream("hello", signed=False))
        self.commit_build()
        builds, log = self.heads()
        self.assertEqual(builds, [])
        self.assertIn("hello: skipping main at", log)

    def test_published_head_is_skipped(self):
        repo = self.upstream("hello", signed=True)
        self.project("hello", repo)
        self.commit_build()
        sha = run("git", "-C", repo, "rev-parse", "--short=7", "HEAD", env=self.env).stdout.strip()
        index = self.apt / "continuous/ubuntu-resolute/dists/resolute/main/binary-amd64/Packages"
        index.parent.mkdir(parents=True)
        index.write_text(f"Package: libhello1\nSource: hello\nVersion: 0+git1.g{sha}-0oos1\n\n")
        builds, log = self.heads()
        self.assertEqual(builds, [])
        self.assertIn("already published", log)

    def test_only_enabled_projects_and_polled_ones_when_polling(self):
        self.project("off", self.upstream("off", signed=True), enabled=False)
        self.project("pushed", self.upstream("pushed", signed=True), push_dispatch=True)
        self.project("polled", self.upstream("polled", signed=True))
        self.commit_build()
        self.assertEqual([b["project"] for b in self.heads()[0]], ["polled", "pushed"])
        self.assertEqual([b["project"] for b in self.heads("--polled-only")[0]], ["polled"])
        self.assertEqual([b["project"] for b in self.heads("--project", "pushed")[0]], ["pushed"])

    def test_requested_project_must_be_enabled(self):
        self.project("off", self.upstream("off", signed=True), enabled=False)
        self.commit_build()
        result = subprocess.run([str(self.root / "ci" / "continuous-heads"), "--project", "off"],
                                capture_output=True, text=True, env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("doesn't have continuous.enabled", result.stderr)


if __name__ == "__main__":
    unittest.main()
