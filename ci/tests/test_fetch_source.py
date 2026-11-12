"""Tests ci/fetch-source's git mode against a local upstream repository with signed tags,
and dependency projects' pinned commits.

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


class FetchSourceFixture(unittest.TestCase):
    """A scratch openocean-build with one project, hello, and its local upstream repository."""

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


class FetchSourceGitTest(FetchSourceFixture):
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

    def commit(self, message):
        (self.upstream / "hello.c").write_text(f"/* {message} */\n")
        run("git", "-C", self.upstream, "commit", "-q", "-am", message, env=self.env)
        return run("git", "-C", self.upstream, "rev-parse", "HEAD", env=self.env).stdout.strip()

    def continuous_version(self, commit):
        # The packaging revision counts this scratch openocean-build's commits to the project
        if not (self.root / ".git").exists():
            run("git", "init", "-q", self.root, env=self.env)
            run("git", "-C", self.root, "add", ".", env=self.env)
            run("git", "-C", self.root, "commit", "-q", "-m", "add hello", env=self.env)
        return run(self.root / "ci" / "project-config", "hello", "continuous-version", self.upstream, commit,
                   env=self.env).stdout.strip()

    def test_continuous_version_counts_from_the_nearest_release_tag(self):
        self.project("1.2.0-1")
        first = run("git", "-C", self.upstream, "rev-parse", "--short=7", "HEAD", env=self.env).stdout.strip()
        self.assertEqual(self.continuous_version("HEAD"), f"0+git1.g{first}-0oos1")
        self.tag("v1.2.0-beta.1", self.maint)
        self.assertEqual(self.continuous_version("HEAD"), f"1.2.0~beta.1+git0.g{first}-0oos1")
        self.commit("two")
        sha = self.commit("three")
        self.assertEqual(self.continuous_version(sha), f"1.2.0~beta.1+git2.g{sha[:7]}-0oos1")
        self.tag("v1.2.0", self.maint)
        self.assertEqual(self.continuous_version(sha), f"1.2.0+git0.g{sha[:7]}-0oos1")
        self.tag("unrelated-tag")
        self.assertEqual(self.continuous_version(sha), f"1.2.0+git0.g{sha[:7]}-0oos1")

    def test_continuous_versions_sort_between_releases(self):
        self.project("1.2.0-1")
        self.tag("v1.2.0-beta.1", self.maint)
        sha = self.commit("two")
        version = self.continuous_version(sha)
        for lower, higher in [("1.2.0~beta.1-1", version), (version, "1.2.0-1"), (version, "1.2.0~beta.2-1")]:
            with self.subTest(lower=lower, higher=higher):
                run("dpkg", "--compare-versions", lower, "lt", higher)

    def test_ref_fetches_a_commit_with_a_staged_changelog(self):
        self.project("1.2.0-1")
        self.tag("v1.2.0", self.maint)
        sha = self.commit("unreleased")
        staged = self.tmp / "staged"
        version = "1.2.0+git1.g" + sha[:7] + "-0oos1"
        shutil.copy2(ROOT / "ci" / "stage-debian", self.root / "ci" / "stage-debian")
        run(self.root / "ci" / "stage-debian", "hello", version, staged, "Continuous build.", env=self.env)
        self.assertIn(f"hello ({version})", (staged / "debian" / "changelog").read_text().splitlines()[0])
        result = subprocess.run([str(self.root / "ci" / "fetch-source"), "--debian-dir", str(staged / "debian"),
                                 "--ref", sha, "hello", str(self.tmp / "out")],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        tarball = self.tmp / "out" / f"hello_1.2.0+git1.g{sha[:7]}.orig.tar.gz"
        with tarfile.open(tarball) as t:
            hello = t.extractfile(f"hello-1.2.0+git1.g{sha[:7]}/hello.c").read().decode()
        self.assertEqual(hello, "/* unreleased */\n")

    def test_missing_tag_fails(self):
        self.project("9.9.9-1")
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v9.9.9", result.stderr)


class FetchSourcePinnedTest(FetchSourceFixture):
    """A project in dependencies/ is released from release.commit, which nobody signs.

    The upstream here, like GitHub, serves commits that no branch or tag points at.
    """

    def setUp(self):
        super().setUp()
        # Local submodules need the file protocol, which git disables for them by default
        self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="protocol.file.allow", GIT_CONFIG_VALUE_0="always")
        run("git", "-C", self.upstream, "config", "uploadpack.allowReachableSHA1InWant", "true", env=self.env)

    def dependency(self, version, commit, tag_format="null", submodules=False):
        shutil.rmtree(self.root / "projects", ignore_errors=True)
        d = self.root / "dependencies" / "hello"
        (d / "debian").mkdir(parents=True, exist_ok=True)
        (d / "project.yaml").write_text(textwrap.dedent(f"""\
            upstream:
              git: {self.upstream}
              tag_format: {tag_format}
              submodules: {str(submodules).lower()}
            source: git
            release:
              commit: {commit}
            """))
        (d / "debian" / "changelog").write_text(textwrap.dedent(f"""\
            hello ({version}) unstable; urgency=medium

              * Test.

             -- T <t@example.org>  Wed, 28 Oct 2026 00:00:00 +0000
            """))
        (d / "debian" / "copyright").write_text("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
                                                "\nFiles: *\nCopyright: T\nLicense: MIT\n")

    def dated_commit(self, repo, message, date):
        (repo / "hello.c").write_text(f"/* {message} */\n")
        run("git", "-C", repo, "add", ".", env=self.env)
        run("git", "-C", repo, "commit", "-q", "-m", message, env=dict(self.env, GIT_COMMITTER_DATE=date))
        return run("git", "-C", repo, "rev-parse", "HEAD", env=self.env).stdout.strip()

    def hello(self, version, path="hello.c"):
        with tarfile.open(self.out / f"hello_{version}.orig.tar.gz") as t:
            return t.extractfile(f"hello-{version}/{path}").read().decode()

    def test_untagged_pin_fetches_that_commit_not_the_head(self):
        # 23:30 on the 6th in UTC-3 is the 7th in UTC, which the version's date is in
        pinned = self.dated_commit(self.upstream, "pinned", "2026-05-06T23:30:00-03:00")
        self.dated_commit(self.upstream, "newer", "2026-06-01T00:00:00Z")
        version = f"2.0.0+git20260507.{pinned[:7]}"
        self.dependency(f"{version}-0oos1", pinned)
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"the source is release.commit {pinned}", result.stdout)
        self.assertEqual(self.hello(version), "/* pinned */\n")

    def test_untagged_pin_with_another_date_is_refused(self):
        pinned = self.dated_commit(self.upstream, "pinned", "2026-05-07T12:00:00Z")
        self.dependency(f"2.0.0+git20260508.{pinned[:7]}-0oos1", pinned)
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{pinned[:7]} was committed on 20260507, but the version is", result.stderr)

    def test_untagged_version_naming_another_commit_is_refused(self):
        pinned = self.dated_commit(self.upstream, "pinned", "2026-05-07T12:00:00Z")
        self.dependency("2.0.0+git20260507.1234567-0oos1", pinned)
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"names commit 1234567, but release.commit is {pinned[:7]}", result.stderr)

    def test_tag_must_point_at_the_pinned_commit(self):
        pinned = self.dated_commit(self.upstream, "pinned", "2026-05-07T12:00:00Z")
        self.dated_commit(self.upstream, "moved", "2026-05-08T12:00:00Z")
        # A tag moved after review, or a pin that names the wrong commit
        self.tag("v1.0.0")
        self.dependency("1.0.0-0oos1", pinned, tag_format='"v{version}"')
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"not release.commit {pinned}", result.stderr)
        self.assertFalse(list(self.out.glob("*.orig.tar.*")) if self.out.exists() else [])

    def test_tag_at_the_pinned_commit_is_fetched(self):
        pinned = self.dated_commit(self.upstream, "pinned", "2026-05-07T12:00:00Z")
        self.tag("v1.0.0")
        self.dependency("1.0.0-0oos1", pinned, tag_format='"v{version}"')
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"v1.0.0 is release.commit {pinned}", result.stdout)
        self.assertEqual(self.hello("1.0.0"), "/* pinned */\n")

    def test_submodules_are_included_at_the_commit_the_source_records(self):
        sub = self.tmp / "sub"
        run("git", "init", "-q", sub, env=self.env)
        run("git", "-C", sub, "config", "uploadpack.allowReachableSHA1InWant", "true", env=self.env)
        self.dated_commit(sub, "recorded", "2026-05-01T00:00:00Z")
        run("git", "-C", self.upstream, "submodule", "add", "-q", sub, "generator", env=self.env)
        pinned = self.dated_commit(self.upstream, "with submodule", "2026-05-07T12:00:00Z")
        # The submodule moves on; the pinned source still records the old commit
        self.dated_commit(sub, "newer", "2026-06-01T00:00:00Z")
        version = f"2.0.0+git20260507.{pinned[:7]}"
        self.dependency(f"{version}-0oos1", pinned, submodules=True)
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.hello(version, "generator/hello.c"), "/* recorded */\n")
        first = (self.out / f"hello_{version}.orig.tar.gz").read_bytes()
        # Fetched again (as each build target does), the tarball is the same
        shutil.rmtree(self.out)
        self.assertEqual(self.fetch().returncode, 0)
        self.assertEqual((self.out / f"hello_{version}.orig.tar.gz").read_bytes(), first)



if __name__ == "__main__":
    unittest.main()
