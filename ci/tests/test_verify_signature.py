"""Tests ci/verify-signature with throwaway GPG and SSH keys, signed tags and commits.

Needs git, gpg and ssh-keygen. Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import os
import re
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "ci" / "verify-signature"


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs)


class VerifySignatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # verify-signature reads projects/ next to its own ci/, so run a copy in a scratch tree
        self.root = self.tmp / "build"
        (self.root / "ci").mkdir(parents=True)
        (self.root / "ci" / "verify-signature").write_text(VERIFY.read_text())
        (self.root / "ci" / "verify-signature").chmod(0o755)

        # The signer's own keyring, separate from the one verify-signature builds
        self.signer_home = self.tmp / "signer-gnupg"
        self.signer_home.mkdir(mode=0o700)
        self.env = dict(os.environ, GNUPGHOME=str(self.signer_home), GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.org",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.org")

        self.repo = self.tmp / "upstream"
        run("git", "init", "-q", self.repo, env=self.env)
        (self.repo / "README").write_text("hello\n")
        run("git", "-C", self.repo, "add", "README", env=self.env)
        run("git", "-C", self.repo, "commit", "-q", "-m", "first", env=self.env)

    def gpg_key(self, email, expire="never"):
        run("gpg", "--batch", "--passphrase", "", "--quick-gen-key", email, "ed25519", "sign", expire, env=self.env)
        out = run("gpg", "--batch", "--with-colons", "--list-secret-keys", email, env=self.env).stdout
        fpr = re.search(r"^fpr:+([0-9A-F]+):", out, re.M).group(1)
        armored = run("gpg", "--batch", "--armor", "--export", fpr, env=self.env).stdout
        return fpr, armored

    def ssh_key(self, name):
        key = self.tmp / name
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
        return key, (self.tmp / f"{name}.pub").read_text().strip()

    def project(self, name, maintainers_yaml, keys=()):
        d = self.root / "projects" / name
        (d / "keys").mkdir(parents=True)
        (d / "maintainers.yaml").write_text(textwrap.dedent(maintainers_yaml))
        for i, armored in enumerate(keys):
            (d / "keys" / f"{i}.asc").write_text(armored)

    def gpg_tag(self, tag, fpr):
        run("git", "-C", self.repo, "tag", "-s", "-u", fpr, "-m", tag, tag, env=self.env)

    def ssh_tag(self, tag, key):
        run("git", "-C", self.repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}",
            "tag", "-s", "-m", tag, tag, env=self.env)

    def verify(self, project, ref):
        return subprocess.run([str(self.root / "ci" / "verify-signature"), project, str(self.repo), ref],
                              capture_output=True, text=True, env=self.env)

    def assertTrusted(self, result, who):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"is signed by {who}", result.stdout)

    def assertRejected(self, result, reason):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stderr)

    def test_tag_signed_by_a_gpg_maintainer(self):
        fpr, armored = self.gpg_key("maint@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        self.gpg_tag("v1.0.0", fpr)
        self.assertTrusted(self.verify("p", "v1.0.0"), "Maint")

    def test_fingerprint_may_be_written_with_spaces(self):
        fpr, armored = self.gpg_key("maint@example.org")
        spaced = " ".join(fpr[i:i + 4] for i in range(0, 40, 4))
        self.project("p", f'maintainers:\n  - name: Maint\n    gpg: ["{spaced}"]\n', [armored])
        self.gpg_tag("v1.0.0", fpr)
        self.assertTrusted(self.verify("p", "v1.0.0"), "Maint")

    def test_tag_signed_by_an_ssh_maintainer(self):
        key, public = self.ssh_key("maint")
        self.project("p", f'maintainers:\n  - name: Maint\n    github: maint\n    ssh: ["{public}"]\n')
        self.ssh_tag("v1.0.0", key)
        self.assertTrusted(self.verify("p", "v1.0.0"), "Maint")

    def test_gpg_key_of_someone_else_is_rejected(self):
        fpr, armored = self.gpg_key("maint@example.org")
        other, _ = self.gpg_key("other@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        self.gpg_tag("v1.0.0", other)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't signed by a maintainer")

    def test_ssh_key_of_someone_else_is_rejected(self):
        _, public = self.ssh_key("maint")
        other, _ = self.ssh_key("other")
        self.project("p", f'maintainers:\n  - name: Maint\n    github: maint\n    ssh: ["{public}"]\n')
        self.ssh_tag("v1.0.0", other)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't signed by a maintainer")

    def test_maintainer_of_another_project_is_rejected(self):
        fpr, armored = self.gpg_key("maint@example.org")
        other, other_armored = self.gpg_key("other@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        self.project("q", f"maintainers:\n  - name: Other\n    gpg: [{other}]\n", [other_armored])
        self.gpg_tag("v1.0.0", other)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't signed by a maintainer")
        self.assertTrusted(self.verify("q", "v1.0.0"), "Other")

    def test_key_in_keys_but_not_listed_is_rejected(self):
        fpr, armored = self.gpg_key("maint@example.org")
        other, other_armored = self.gpg_key("other@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored, other_armored])
        self.gpg_tag("v1.0.0", other)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't a maintainer key")

    def test_expired_key_is_rejected(self):
        fpr, armored = self.gpg_key("old@example.org", expire="seconds=1")
        self.gpg_tag("v1.0.0", fpr)
        self.project("p", f"maintainers:\n  - name: Old\n    gpg: [{fpr}]\n", [armored])
        time.sleep(2)
        self.assertRejected(self.verify("p", "v1.0.0"), "EXPKEYSIG")

    def test_unsigned_annotated_tag_is_rejected(self):
        fpr, armored = self.gpg_key("maint@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        run("git", "-C", self.repo, "tag", "-a", "-m", "v1.0.0", "v1.0.0", env=self.env)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't signed by a maintainer")

    def test_lightweight_tag_of_an_unsigned_commit_is_rejected(self):
        fpr, armored = self.gpg_key("maint@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        run("git", "-C", self.repo, "tag", "v1.0.0", env=self.env)
        self.assertRejected(self.verify("p", "v1.0.0"), "isn't signed by a maintainer")

    def test_signed_commit(self):
        fpr, armored = self.gpg_key("maint@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n", [armored])
        run("git", "-C", self.repo, "commit", "-q", "--allow-empty", "-S" + fpr, "-m", "signed", env=self.env)
        self.assertTrusted(self.verify("p", "HEAD"), "Maint")

    def test_listed_fingerprint_needs_its_key(self):
        fpr, _ = self.gpg_key("maint@example.org")
        self.project("p", f"maintainers:\n  - name: Maint\n    gpg: [{fpr}]\n")
        self.gpg_tag("v1.0.0", fpr)
        self.assertRejected(self.verify("p", "v1.0.0"), "no key in projects/p/keys/")

    def test_project_without_maintainers_trusts_nothing(self):
        (self.root / "projects" / "p").mkdir(parents=True)
        self.assertRejected(self.verify("p", "HEAD"), "has no maintainers.yaml")


if __name__ == "__main__":
    unittest.main()
