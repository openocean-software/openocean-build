"""Tests ci/fetch-source's git mode against a local upstream repository with signed tags.

Needs git, gpg, python3-yaml and devscripts (dpkg-parsechangelog, mk-origtargz).
Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs)


class FetchSourceGitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # A scratch copy of openocean-build with one test project
        self.root = self.tmp / "build"
        (self.root / "ci").mkdir(parents=True)
        for script in ("fetch-source", "project-config", "verify-signature"):
            shutil.copy2(ROOT / "ci" / script, self.root / "ci" / script)
        shutil.copy2(ROOT / "defaults.yaml", self.root / "defaults.yaml")

        gnupghome = self.tmp / "signer-gnupg"
        gnupghome.mkdir(mode=0o700)
        self.env = dict(os.environ, GNUPGHOME=str(gnupghome), GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.org",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.org")
        self.maint, self.maint_key = self.gpg_key("maint@example.org")
        self.other, _ = self.gpg_key("other@example.org")

        self.upstream = self.tmp / "upstream"
        run("git", "init", "-q", self.upstream, env=self.env)
        (self.upstream / "hello.c").write_text("int main(void) { return 0; }\n")
        (self.upstream / "vendored").mkdir()
        (self.upstream / "vendored" / "blob").write_text("not free\n")
        run("git", "-C", self.upstream, "add", ".", env=self.env)
        run("git", "-C", self.upstream, "commit", "-q", "-m", "first", env=self.env)

    def gpg_key(self, email):
        run("gpg", "--batch", "--passphrase", "", "--quick-gen-key", email, "ed25519", "sign", "never", env=self.env)
        out = run("gpg", "--batch", "--with-colons", "--list-secret-keys", email, env=self.env).stdout
        fpr = re.search(r"^fpr:+([0-9A-F]+):", out, re.M).group(1)
        return fpr, run("gpg", "--batch", "--armor", "--export", fpr, env=self.env).stdout

    def project(self, version, require_signed_tag=True, files_excluded=None):
        d = self.root / "projects" / "hello"
        (d / "debian").mkdir(parents=True, exist_ok=True)
        (d / "keys").mkdir(exist_ok=True)
        (d / "project.yaml").write_text(textwrap.dedent(f"""\
            upstream:
              git: {self.upstream}
              tag_format: "v{{version}}"
            source: git
            release:
              require_signed_tag: {str(require_signed_tag).lower()}
            """))
        (d / "maintainers.yaml").write_text(f"maintainers:\n  - name: Maint\n    gpg: [{self.maint}]\n")
        (d / "keys" / "maint.asc").write_text(self.maint_key)
        (d / "debian" / "changelog").write_text(textwrap.dedent(f"""\
            hello ({version}) unstable; urgency=medium

              * Test.

             -- T <t@example.org>  Wed, 28 Oct 2026 00:00:00 +0000
            """))
        copyright = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\nUpstream-Name: hello\n"
        if files_excluded:
            copyright += f"Files-Excluded: {files_excluded}\n"
        (d / "debian" / "copyright").write_text(copyright + "\nFiles: *\nCopyright: T\nLicense: MIT\n")

    def tag(self, name, signer=None):
        if signer:
            run("git", "-C", self.upstream, "tag", "-s", "-u", signer, "-m", name, name, env=self.env)
        else:
            run("git", "-C", self.upstream, "tag", "-a", "-m", name, name, env=self.env)

    def fetch(self):
        self.out = self.tmp / "out"
        return subprocess.run([str(self.root / "ci" / "fetch-source"), "hello", str(self.out)],
                              capture_output=True, text=True, env=self.env)

    def members(self, tarball):
        with tarfile.open(tarball) as t:
            return sorted(m.name for m in t.getmembers() if m.isfile())

    def test_signed_tag_becomes_the_orig_tarball(self):
        self.project("1.2.0-1")
        self.tag("v1.2.0", self.maint)
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("refs/tags/v1.2.0 is signed by Maint", result.stdout)
        self.assertEqual(self.members(self.out / "hello_1.2.0.orig.tar.gz"),
                         ["hello-1.2.0/hello.c", "hello-1.2.0/vendored/blob"])

    def test_tag_signed_by_someone_else_is_refused(self):
        self.project("1.2.0-1")
        self.tag("v1.2.0", self.other)
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("isn't signed by a maintainer", result.stderr)
        self.assertFalse(list(self.out.glob("*.orig.tar.*")) if self.out.exists() else [])

    def test_unsigned_tag_is_fetched_when_signatures_are_not_required(self):
        self.project("1.2.0-1", require_signed_tag=False)
        self.tag("v1.2.0")
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "hello_1.2.0.orig.tar.gz").is_file())

    def test_prerelease_version_maps_to_a_dashed_tag(self):
        self.project("1.2.0~rc.1-1")
        self.tag("v1.2.0-rc.1", self.maint)
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "hello_1.2.0~rc.1.orig.tar.gz").is_file())

    def test_files_excluded_are_removed(self):
        self.project("1.2.0+dfsg-1", files_excluded="vendored/*")
        self.tag("v1.2.0", self.maint)
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.members(self.out / "hello_1.2.0+dfsg.orig.tar.gz"),
                         ["hello-1.2.0+dfsg/hello.c"])

    def test_missing_tag_fails(self):
        self.project("9.9.9-1")
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v9.9.9", result.stderr)


if __name__ == "__main__":
    unittest.main()
