"""Tests ci/project-config: merging defaults.yaml with a project's settings, and the build matrix.

Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import importlib.machinery
import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# project-config has no .py extension, so load it by path
_loader = importlib.machinery.SourceFileLoader("project_config", str(ROOT / "ci" / "project-config"))
_spec = importlib.util.spec_from_loader("project_config", _loader)
project_config = importlib.util.module_from_spec(_spec)
_loader.exec_module(project_config)

DEFAULTS = """
targets:
  arch: [amd64, arm64]
  dists:
    - { distro: debian, dist: trixie }
    - { distro: ubuntu, dist: resolute }
channels:
  release: { prefix: release/, environment: release }
  beta: { prefix: beta/, environment: release }
upstream:
  tag_format: "v{version}"
release:
  require_signed_tag: false
  beta_tags: null
install_test:
  target: { distro: ubuntu, dist: resolute, arch: amd64 }
  packages: []
  commands: []
"""


class ProjectConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "defaults.yaml").write_text(DEFAULTS)

    def project(self, name, project_yaml, changelog=True):
        d = self.root / "projects" / name
        (d / "debian").mkdir(parents=True)
        (d / "project.yaml").write_text(textwrap.dedent(project_yaml))
        if changelog:
            (d / "debian" / "changelog").write_text("")
        return name

    def load(self, name):
        return project_config.load(name, root=self.root)

    def test_project_overrides_merge_into_defaults(self):
        config = self.load(self.project("p", """
            source: uscan
            install_test:
              commands: [true]
            """))
        self.assertEqual(config["install_test"]["commands"], [True])
        # Sibling keys the project didn't set are kept
        self.assertEqual(config["install_test"]["target"]["dist"], "resolute")
        self.assertEqual(config["channels"]["release"]["prefix"], "release/")

    def test_lists_are_replaced_not_merged(self):
        config = self.load(self.project("p", """
            source: uscan
            targets:
              arch: [amd64]
            """))
        self.assertEqual(config["targets"]["arch"], ["amd64"])
        self.assertEqual(len(config["targets"]["dists"]), 2)

    def test_matrix_marks_the_install_test_target(self):
        config = self.load(self.project("p", "source: uscan\n"))
        targets = project_config.matrix(config)
        self.assertEqual(len(targets), 4)
        self.assertEqual([t for t in targets if t["install_test"]],
                         [{"distro": "ubuntu", "dist": "resolute", "arch": "amd64", "install_test": True}])
        self.assertEqual(project_config.matrix(config, install_test_only=True),
                         [t for t in targets if t["install_test"]])

    def test_excluded_targets_are_not_built(self):
        config = self.load(self.project("p", """
            source: uscan
            targets:
              exclude:
                - { distro: debian, dist: trixie }
                - { distro: ubuntu, dist: resolute, arch: arm64 }
            """))
        self.assertEqual([(t["dist"], t["arch"]) for t in project_config.matrix(config)],
                         [("resolute", "amd64")])

    def test_install_test_target_must_be_built(self):
        with self.assertRaisesRegex(project_config.ConfigError, "install_test.target"):
            self.load(self.project("p", """
                source: uscan
                targets:
                  dists:
                    - { distro: debian, dist: trixie }
                """))

    def test_excluding_the_install_test_target_is_rejected(self):
        with self.assertRaisesRegex(project_config.ConfigError, "install_test.target"):
            self.load(self.project("p", """
                source: uscan
                targets:
                  exclude:
                    - { distro: ubuntu, dist: resolute }
                """))

    def test_unknown_source_is_rejected(self):
        with self.assertRaisesRegex(project_config.ConfigError, "source must be one of"):
            self.load(self.project("p", "source: svn\n"))

    def test_missing_changelog_is_rejected(self):
        with self.assertRaisesRegex(project_config.ConfigError, "missing debian/changelog"):
            self.load(self.project("p", "source: uscan\n", changelog=False))

    def test_install_script_installs_tools_after_the_packages_and_runs_commands(self):
        config = self.load(self.project("p", """
            source: uscan
            install_test:
              packages: [cmake, g++]
              commands:
                - cmake --version
            """))
        self.assertEqual(project_config.install_script(config).splitlines()[1:], [
            "set -euxo pipefail",
            "apt-get install -y --no-install-recommends cmake g++",
            "cmake --version",
        ])

    def test_get_reads_nested_settings(self):
        config = self.load(self.project("p", "source: uscan\n"))
        self.assertEqual(project_config.get(config, "channels.release.environment"), "release")
        with self.assertRaisesRegex(project_config.ConfigError, "no setting"):
            project_config.get(config, "channels.continuous.prefix")

    def test_release_tag_maps_debian_versions_to_upstream_tags(self):
        config = self.load(self.project("p", "source: uscan\n"))
        for version, tag in [("1.2.0", "v1.2.0"), ("24.8.1+dfsg", "v24.8.1"), ("1.2.0+ds1", "v1.2.0"),
                             ("0.1.0~beta.1", "v0.1.0-beta.1"), ("2.0.0~rc.2+dfsg", "v2.0.0-rc.2")]:
            with self.subTest(version=version):
                self.assertEqual(project_config.release_tag(config, version), tag)

    def test_tag_format_is_per_project(self):
        config = self.load(self.project("p", 'source: uscan\nupstream:\n  tag_format: "release-{version}"\n'))
        self.assertEqual(project_config.release_tag(config, "3.1"), "release-3.1")

    def test_channel_is_beta_only_for_matching_tags(self):
        config = self.load(self.project("p", """
            source: uscan
            release:
              beta_tags: '^v[0-9.]+-(beta|rc)\\.[0-9]+$'
            """))
        self.assertEqual(project_config.channel(config, "0.1.0~beta.1"), "beta")
        self.assertEqual(project_config.channel(config, "0.1.0~rc.2"), "beta")
        self.assertEqual(project_config.channel(config, "0.1.0"), "release")

    def test_prerelease_without_beta_tags_is_refused(self):
        config = self.load(self.project("p", "source: uscan\n"))
        with self.assertRaisesRegex(project_config.ConfigError, "prerelease"):
            project_config.channel(config, "0.1.0~beta.1")

    def test_prerelease_not_matching_beta_tags_is_refused(self):
        config = self.load(self.project("p", """
            source: uscan
            release:
              beta_tags: '-rc\\.[0-9]+$'
            """))
        with self.assertRaisesRegex(project_config.ConfigError, "prerelease"):
            project_config.channel(config, "0.1.0~alpha.1")

    def test_beta_tags_needs_a_beta_channel(self):
        (self.root / "defaults.yaml").write_text(DEFAULTS.replace("  beta: { prefix: beta/, environment: release }\n", ""))
        with self.assertRaisesRegex(project_config.ConfigError, "beta channel"):
            self.load(self.project("p", "source: uscan\nrelease:\n  beta_tags: beta\n"))

    def test_every_real_project_loads(self):
        for name in project_config.list_projects():
            with self.subTest(project=name):
                project_config.load(name)


if __name__ == "__main__":
    unittest.main()
