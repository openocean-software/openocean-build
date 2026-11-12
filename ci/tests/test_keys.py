"""Checks the keys in the repository against defaults.yaml, and scripts/apt-setup against both.

Needs gpg and python3-yaml. Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = yaml.safe_load((ROOT / "defaults.yaml").read_text())


def fingerprints(key):
    out = subprocess.run(["gpg", "--show-keys", "--with-colons", str(ROOT / key)],
                         check=True, capture_output=True, text=True).stdout
    return set(re.findall(r"^fpr:+([0-9A-F]+):", out, re.M))


def bash_array(script, name):
    """The [key]=value entries of a bash associative array declared in script."""
    body = re.search(rf"declare -A {name}=\((.*?)\n\)", script, re.S)[1]
    return dict(re.findall(r"\[(\w+)\]=(\S+)", body))


class KeysTest(unittest.TestCase):
    def test_each_channel_key_holds_its_signing_key(self):
        for name, channel in DEFAULTS["channels"].items():
            with self.subTest(channel=name):
                self.assertIn(channel["signing_key"], fingerprints(channel["key"]))

    def test_each_repository_key_has_its_fingerprint(self):
        for name, repository in DEFAULTS["repositories"].items():
            with self.subTest(repository=name):
                self.assertIn(repository["fingerprint"], fingerprints(repository["key"]))


class AptSetupTest(unittest.TestCase):
    """apt-setup carries its own copy of the settings users need; they must match defaults.yaml."""

    def setUp(self):
        self.script = (ROOT / "scripts" / "apt-setup").read_text()

    def variable(self, name):
        return re.search(rf"^{name}=(.*)$", self.script, re.M)[1].strip('"')

    def test_channels_match(self):
        paths, prints = bash_array(self.script, "KEY_PATH"), bash_array(self.script, "KEY_FINGERPRINT")
        self.assertEqual(self.variable("APT_URL"), DEFAULTS["apt_url"])
        for name, channel in DEFAULTS["channels"].items():
            with self.subTest(channel=name):
                self.assertEqual(channel["prefix"], f"{name}/")
                self.assertEqual(paths[name], channel["key"])
                self.assertEqual(prints[name], channel["signing_key"])

    def test_zenoh_matches(self):
        zenoh = DEFAULTS["repositories"]["zenoh"]
        paths, prints = bash_array(self.script, "KEY_PATH"), bash_array(self.script, "KEY_FINGERPRINT")
        self.assertEqual(paths["zenoh"], zenoh["key"])
        self.assertEqual(prints["zenoh"], zenoh["fingerprint"])
        self.assertEqual(self.variable("ZENOH_URL"), zenoh["url"])
        self.assertEqual(self.variable("ZENOH_SUITE"), zenoh["suite"])
        self.assertEqual(self.variable("ZENOH_PIN_PACKAGES").split(), zenoh["pin"]["packages"])
        self.assertEqual(self.variable("ZENOH_PIN_VERSION"), str(zenoh["pin"]["version"]))


if __name__ == "__main__":
    unittest.main()
