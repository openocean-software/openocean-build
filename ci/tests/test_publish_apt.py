"""Tests ci/publish-apt against a file:// target, with throwaway packages and a throwaway signing key.

Needs aptly, gpg and dpkg-deb. Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLISH_APT = ROOT / "ci" / "publish-apt"


def make_deb(dest, name, version, arch):
    """Builds a minimal binary package into dest."""
    pkg = dest / f"{name}-{version}-{arch}"
    (pkg / "DEBIAN").mkdir(parents=True)
    (pkg / "DEBIAN" / "control").write_text(
        f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
        f"Maintainer: Test <test@example.org>\nDescription: test package\n")
    (pkg / "usr" / "share" / "doc" / name).mkdir(parents=True)
    (pkg / "usr" / "share" / "doc" / name / "README").write_text(f"{name} {version}\n")
    subprocess.run(["dpkg-deb", "--root-owner-group", "-b", str(pkg), str(dest)],
                   check=True, capture_output=True)


class PublishAptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = self.tmp / "bucket"
        self.env = dict(os.environ, GNUPGHOME=str(self.tmp / "gnupg"))
        (self.tmp / "gnupg").mkdir(mode=0o700)
        subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key",
                        "Test Signing <signing@example.org>", "ed25519", "sign", "never"],
                       env=self.env, check=True, capture_output=True)
        out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-secret-keys"],
                             env=self.env, check=True, capture_output=True, text=True).stdout
        self.key = re.search(r"^fpr:+([0-9A-F]+):", out, re.M).group(1)

    def artifacts(self, name, builds):
        """Lays out build artifacts as build-debian.yml downloads them: builds maps arch to [(package, version)]."""
        root = self.tmp / name
        for arch, packages in builds.items():
            d = root / f"deb_{name}_debian_trixie_{arch}"
            d.mkdir(parents=True)
            for package, version in packages:
                make_deb(d, package, version, arch)
        return root

    def publish(self, artifacts, key=None):
        return subprocess.run(
            [str(PUBLISH_APT), "--target", f"file://{self.target}", "--prefix", "release/",
             "--gpg-key", key or self.key, str(artifacts)],
            env=self.env, capture_output=True, text=True)

    def published(self, arch="amd64"):
        """(package, version) pairs in the published index for one architecture."""
        index = self.target / "release/debian-trixie/dists/trixie/main" / f"binary-{arch}" / "Packages"
        text = index.read_text()
        return set(zip(re.findall(r"^Package: (.+)$", text, re.M), re.findall(r"^Version: (.+)$", text, re.M)))

    def assertPublished(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_second_project_keeps_the_first(self):
        self.assertPublished(self.publish(self.artifacts("one", {"amd64": [("one", "1.0-1")], "arm64": [("one", "1.0-1")]})))
        self.assertPublished(self.publish(self.artifacts("two", {"amd64": [("two", "2.0-1")], "arm64": [("two", "2.0-1")]})))
        self.assertEqual(self.published("amd64"), {("one", "1.0-1"), ("two", "2.0-1")})
        self.assertEqual(self.published("arm64"), {("one", "1.0-1"), ("two", "2.0-1")})

    def test_new_version_keeps_the_old_one(self):
        self.assertPublished(self.publish(self.artifacts("v1", {"amd64": [("one", "1.0-1")]})))
        self.assertPublished(self.publish(self.artifacts("v2", {"amd64": [("one", "1.0-2")]})))
        self.assertEqual(self.published(), {("one", "1.0-1"), ("one", "1.0-2")})

    def test_architecture_all_packages_are_kept(self):
        self.assertPublished(self.publish(self.artifacts("one", {"amd64": [("data", "1.0-1")]})))
        # make_deb uses the directory's arch, so build an "all" package by hand next to an amd64 one
        d = self.tmp / "all" / "deb_all_debian_trixie_amd64"
        d.mkdir(parents=True)
        make_deb(d, "docs", "1.0-1", "all")
        make_deb(d, "tool", "1.0-1", "amd64")
        self.assertPublished(self.publish(self.tmp / "all"))
        self.assertPublished(self.publish(self.artifacts("three", {"amd64": [("three", "3.0-1")]})))
        self.assertEqual(self.published(), {("data", "1.0-1"), ("docs", "1.0-1"), ("tool", "1.0-1"), ("three", "3.0-1")})

    def test_refuses_an_index_signed_by_another_key(self):
        self.assertPublished(self.publish(self.artifacts("one", {"amd64": [("one", "1.0-1")]})))
        subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key",
                        "Other <other@example.org>", "ed25519", "sign", "never"],
                       env=self.env, check=True, capture_output=True)
        out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-secret-keys", "other@example.org"],
                             env=self.env, check=True, capture_output=True, text=True).stdout
        other = re.search(r"^fpr:+([0-9A-F]+):", out, re.M).group(1)
        result = self.publish(self.artifacts("two", {"amd64": [("two", "2.0-1")]}), key=other)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("isn't validly signed", result.stderr)

    def test_refuses_a_tampered_package(self):
        self.assertPublished(self.publish(self.artifacts("one", {"amd64": [("one", "1.0-1")]})))
        deb = next((self.target / "release/debian-trixie/pool").rglob("*.deb"))
        deb.write_bytes(deb.read_bytes() + b"x")
        result = self.publish(self.artifacts("two", {"amd64": [("two", "2.0-1")]}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("doesn't match the checksum", result.stderr)


if __name__ == "__main__":
    unittest.main()
