"""Tests ci/project-config: merging defaults.yaml with a project's settings, and the build matrix.

Run from the repository root:
  python3 -m unittest discover -s ci/tests
"""

import importlib.machinery
import importlib.util
import subprocess
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
apt_url: https://apt.example.org
channels:
  release: { prefix: release/, environment: release, key: keys/release.asc, build_against: [release] }
  beta: { prefix: beta/, environment: release, key: keys/release.asc, build_against: [release, beta] }
repositories:
  ext:
    url: https://ext.example.org/{distro}/
    suite: "{dist}"
    components: [main]
    key: trust/ext.asc
    pin: { packages: [libext, libext-dev], version: 1.2.3 }
build_depends:
  projects: []
  repositories: []
upstream:
  tag_format: "v{version}"
  prerelease_separator: "-"
release:
  enabled: true
  require_signed_tag: false
  beta_tags: null
continuous:
  enabled: false
  accept_github_merges: true
  push_dispatch: false
  branch: null
  version_base: null
install_test:
  target: { distro: ubuntu, dist: resolute, arch: amd64 }
  packages: []
  commands: []
"""


class ProjectFixture(unittest.TestCase):
    """A scratch openocean-build with the defaults above, to add projects to."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "defaults.yaml").write_text(DEFAULTS)
        for key in ("keys/release.asc", "trust/ext.asc"):
            (self.root / key).parent.mkdir(exist_ok=True)
            (self.root / key).write_text(f"-----BEGIN PGP PUBLIC KEY BLOCK-----\n{key}\n")

    def project(self, name, project_yaml, changelog=True, directory="projects"):
        d = self.root / directory / name
        (d / "debian").mkdir(parents=True, exist_ok=True)
        (d / "project.yaml").write_text(textwrap.dedent(project_yaml))
        if changelog:
            (d / "debian" / "changelog").write_text("")
        return name

    def load(self, name):
        return project_config.load(name, root=self.root)


class ProjectConfigTest(ProjectFixture):
    def test_project_overrides_merge_into_defaults(self):
        config = self.load(self.project("p", """
            source: uscan
            install_test:
              commands: ["true"]
            """))
        self.assertEqual(config["install_test"]["commands"], ["true"])
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

    def test_install_test_commands_must_be_strings(self):
        # An unquoted "grep 'a: b'" is a YAML mapping, not a command
        with self.assertRaisesRegex(project_config.ConfigError, "must be a string"):
            self.load(self.project("p", "source: uscan\ninstall_test:\n  commands:\n    - grep -F 'Usage: x'\n"))

    def test_arch_all_must_be_a_built_arch(self):
        self.load(self.project("p", "source: uscan\ntargets:\n  arch_all: amd64\n"))
        with self.assertRaisesRegex(project_config.ConfigError, "targets.arch_all armhf isn't one of targets.arch"):
            self.load(self.project("p", "source: uscan\ntargets:\n  arch_all: armhf\n"))

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

    def test_prerelease_separator_is_per_project(self):
        config = self.load(self.project("p", """
            source: uscan
            upstream:
              tag_format: "{version}"
              prerelease_separator: _
            """))
        self.assertEqual(project_config.release_tag(config, "5.0.0~alpha2"), "5.0.0_alpha2")
        self.assertEqual(project_config.tag_version(config, "5.0.0_alpha2"), "5.0.0~alpha2")
        # Tags that aren't versions are ignored, even when tag_format has no prefix to tell them apart
        self.assertIsNone(project_config.tag_version(config, "debian-5.1.1"))

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
        (self.root / "defaults.yaml").write_text(DEFAULTS.replace(
            "  beta: { prefix: beta/, environment: release, key: keys/release.asc, build_against: [release, beta] }\n", ""))
        with self.assertRaisesRegex(project_config.ConfigError, "beta channel"):
            self.load(self.project("p", "source: uscan\nrelease:\n  beta_tags: beta\n"))

    def test_tag_version_reverses_release_tag(self):
        config = self.load(self.project("p", "source: uscan\n"))
        for tag, version in [("v1.2.0", "1.2.0"), ("v0.0.1-beta.1", "0.0.1~beta.1"), ("other-1.0", None)]:
            with self.subTest(tag=tag):
                self.assertEqual(project_config.tag_version(config, tag), version)

    def test_snapshot_only_projects_need_continuous_builds(self):
        with self.assertRaisesRegex(project_config.ConfigError, "release.enabled false needs continuous.enabled"):
            self.load(self.project("p", "source: git\nupstream:\n  git: https://example.org/p.git\nrelease:\n  enabled: false\n"))

    def test_continuous_builds_need_git_source(self):
        with self.assertRaisesRegex(project_config.ConfigError, "continuous builds need source git"):
            self.load(self.project("p", "source: uscan\ncontinuous:\n  enabled: true\n"))

    def test_build_depends_must_name_projects_and_repositories(self):
        self.project("dep", "source: uscan\n")
        for yaml_text, error in [
            ("build_depends:\n  projects: [nope]\n", "nope, which isn't a project"),
            ("build_depends:\n  projects: [p]\n", "names the project itself"),
            ("build_depends:\n  repositories: [nope]\n", "nope, which isn't in repositories"),
        ]:
            with self.subTest(yaml=yaml_text), self.assertRaisesRegex(project_config.ConfigError, error):
                self.load(self.project("p", "source: uscan\n" + yaml_text))
        self.load(self.project("p", "source: uscan\nbuild_depends:\n  projects: [dep]\n  repositories: [ext]\n"))

    def test_a_dependency_must_build_every_target_its_dependent_does(self):
        self.project("dep", "source: uscan\ntargets:\n  exclude:\n    - { distro: debian, dist: trixie, arch: arm64 }\n")
        with self.assertRaisesRegex(project_config.ConfigError, "builds debian/trixie/arm64, which dep doesn't"):
            self.load(self.project("p", "source: uscan\nbuild_depends:\n  projects: [dep]\n"))
        # Building fewer targets than the dependency is fine
        self.load(self.project("p", "source: uscan\nbuild_depends:\n  projects: [dep]\n"
                                    "targets:\n  exclude:\n    - { distro: debian, dist: trixie }\n"))

    def test_build_order_puts_dependencies_first_even_through_other_projects(self):
        self.project("a", "source: uscan\nbuild_depends:\n  projects: [b]\n")
        self.project("b", "source: uscan\nbuild_depends:\n  projects: [c]\n")
        self.project("c", "source: uscan\n")
        self.project("d", "source: uscan\n")
        self.assertEqual(project_config.build_order(["a", "d", "c"], root=self.root), ["c", "a", "d"])
        self.assertEqual(project_config.release_conflicts(["a", "c", "d"], root=self.root), [("c", "a")])
        self.assertEqual(project_config.release_conflicts(["b", "d"], root=self.root), [])

    def test_build_order_reports_a_cycle(self):
        self.project("a", "source: uscan\nbuild_depends:\n  projects: [b]\n")
        self.project("b", "source: uscan\nbuild_depends:\n  projects: [a]\n")
        with self.assertRaisesRegex(project_config.ConfigError, "cycle: a -> b -> a"):
            project_config.build_order(["a"], root=self.root)

    def test_no_build_depends_needs_no_sources(self):
        config = self.load(self.project("p", "source: uscan\n"))
        self.assertEqual(project_config.build_sources("p", config, "beta", "ubuntu", "resolute",
                                                      probe=lambda url, dist: True), [])

    def test_sources_are_the_published_build_against_channels_and_named_repositories(self):
        self.project("dep", "source: uscan\n")
        config = self.load(self.project("p", "source: uscan\nbuild_depends:\n  projects: [dep]\n  repositories: [ext]\n"))
        # The beta channel hasn't reached this release yet, so only release is used
        sources = project_config.build_sources("p", config, "beta", "ubuntu", "resolute",
                                               probe=lambda url, dist: "/beta/" not in url)
        self.assertEqual([(s["name"], s["uri"], s["suite"], s["components"]) for s in sources], [
            ("openocean-release", "https://apt.example.org/release/ubuntu-resolute", "resolute", ["main"]),
            ("ext", "https://ext.example.org/ubuntu/", "resolute", ["main"]),
        ])
        self.assertEqual(sources[1]["pin"], {"packages": ["libext", "libext-dev"], "version": "1.2.3"})

    def test_sources_are_written_for_apt_and_for_sbuild(self):
        self.project("dep", "source: uscan\n")
        config = self.load(self.project("p", "source: uscan\nbuild_depends:\n  projects: [dep]\n  repositories: [ext]\n"))
        sources = project_config.build_sources("p", config, "release", "debian", "trixie", probe=lambda url, dist: True)
        out = self.root / "out"
        project_config.write_sources(sources, out, "/sources", root=self.root)
        apt = out / "apt"
        self.assertEqual((apt / "sources.list.d" / "ext.sources").read_text(),
                         "Types: deb\nURIs: https://ext.example.org/debian/\nSuites: trixie\nComponents: main\n"
                         "Signed-By: /etc/apt/keyrings/ext.asc\n")
        self.assertIn("trust/ext.asc", (apt / "keyrings" / "ext.asc").read_text())
        self.assertEqual((apt / "preferences.d" / "ext.pref").read_text(),
                         "Package: libext libext-dev\nPin: version 1.2.3\nPin-Priority: 1001\n")
        self.assertFalse((apt / "preferences.d" / "openocean-release.pref").exists())
        # sbuild evaluates its configuration as Perl, with these variables already declared
        perl = r"""
            use strict; use warnings;
            my $extra_repositories = []; my $extra_repository_keys = [];
            my $external_commands = {"chroot-setup-commands" => []};
            my $e = eval `cat "$ARGV[0]"`; die $@ unless defined $e;
            print join("\n", @$extra_repositories), "\n--\n", join("\n", @$extra_repository_keys), "\n--\n";
            print $_ for @{$external_commands->{"chroot-setup-commands"}};
        """
        out_text = subprocess.run(["perl", "-e", perl, str(out / "sbuild.conf")], check=True,
                                  capture_output=True, text=True).stdout
        repositories, keys, commands = out_text.split("\n--\n")
        self.assertEqual(repositories.splitlines(), [
            "deb https://apt.example.org/release/debian-trixie trixie main",
            "deb https://ext.example.org/debian/ trixie main",
        ])
        self.assertEqual(keys.splitlines(), ["/sources/apt/keyrings/openocean-release.asc",
                                             "/sources/apt/keyrings/ext.asc"])
        # The pin is written inside the chroot by a command sbuild runs with /bin/sh -c
        script = commands.replace("%%", "%")
        self.assertNotRegex(commands.replace("%%", ""), "%", "sbuild would expand a lone %")
        written = subprocess.run(["sh", "-c", script.replace("/etc/apt/preferences.d", str(self.root / "pref"))],
                                 check=True, capture_output=True, text=True)
        self.assertEqual(written.returncode, 0)
        self.assertEqual((self.root / "pref" / "ext.pref").read_text(),
                         "Package: libext libext-dev\nPin: version 1.2.3\nPin-Priority: 1001\n")

    def test_resolve_alternatives_is_set_in_sbuild_conf_only_when_asked(self):
        out = self.root / "out"
        project_config.write_sources([], out, root=self.root)
        self.assertNotIn("resolve_alternatives", (out / "sbuild.conf").read_text())
        project_config.write_sources([], out, root=self.root, resolve_alternatives=True)
        # sbuild evaluates its configuration as Perl, with its settings already declared
        perl = 'my $resolve_alternatives = 0; my $extra_repositories; my $extra_repository_keys; ' \
               'my $e = eval `cat "$ARGV[0]"`; die $@ unless defined $e; print $resolve_alternatives'
        result = subprocess.run(["perl", "-e", perl, str(out / "sbuild.conf")], check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout, "1")

    def test_every_real_project_loads(self):
        for name in project_config.list_projects():
            with self.subTest(project=name):
                project_config.load(name)

    def test_every_real_project_is_named_after_its_source_package(self):
        # The Makefile, fetch-source and continuous-heads all find a project's files by its source package name
        for name in project_config.list_projects():
            with self.subTest(project=name):
                changelog = ROOT / project_config.project_dir(name) / "debian" / "changelog"
                source = subprocess.run(["dpkg-parsechangelog", "-l", str(changelog), "-S", "Source"],
                                        check=True, capture_output=True, text=True).stdout.strip()
                self.assertEqual(source, name)


    def test_every_real_dependency_version_names_its_pinned_commit(self):
        for name in project_config.list_projects():
            config = project_config.load(name)
            if config["release"]["commit"]:
                with self.subTest(project=name):
                    project_config.pinned_commit(name, config, project_config.upstream_version(name))


class DependencyProjectTest(ProjectFixture):
    """Projects in dependencies/: found next to projects/, and released from a pinned commit."""

    COMMIT = "4b03484ee7b296c8f1ecd50ece4b9893baff90e8"

    def dependency(self, name="dep", extra=""):
        return self.project(name, f"""
            upstream:
              git: https://example.org/dep.git
              tag_format: null
            source: git
            release:
              commit: {self.COMMIT}
            {extra}""", directory="dependencies")

    def test_projects_are_listed_from_both_directories(self):
        self.project("member", "source: uscan\n")
        self.dependency()
        self.assertEqual(project_config.list_projects(self.root), ["dep", "member"])
        self.assertEqual(str(project_config.project_dir("dep", self.root)), "dependencies/dep")
        self.assertEqual(str(project_config.project_dir("member", self.root)), "projects/member")
        self.load("dep")

    def test_a_name_in_both_directories_is_refused(self):
        self.project("dep", "source: uscan\n")
        self.dependency()
        with self.assertRaisesRegex(project_config.ConfigError, "dep is in both projects/ and dependencies/"):
            project_config.list_projects(self.root)

    def test_a_dependency_needs_a_full_pinned_commit(self):
        self.project("dep", """
            upstream: { git: https://example.org/dep.git }
            source: git
            """, directory="dependencies")
        with self.assertRaisesRegex(project_config.ConfigError, "needs source git and release.commit"):
            self.load("dep")
        self.project("dep", """
            upstream: { git: https://example.org/dep.git }
            source: git
            release: { commit: 4b03484 }
            """, directory="dependencies")
        with self.assertRaisesRegex(project_config.ConfigError, "full 40-character commit hash"):
            self.load("dep")

    def test_a_dependency_has_no_continuous_builds(self):
        self.dependency(extra="continuous: { enabled: true }")
        (self.root / "dependencies" / "dep" / "maintainers.yaml").write_text("maintainers: []\n")
        with self.assertRaisesRegex(project_config.ConfigError, "released from release.commit only"):
            self.load("dep")

    def test_a_member_project_cant_pin_a_commit(self):
        # Members sign their tags (the plan's Decision 7): no pinned-snapshot path for them
        self.project("member", f"""
            upstream: {{ git: https://example.org/member.git }}
            source: git
            release: {{ commit: {self.COMMIT} }}
            """)
        with self.assertRaisesRegex(project_config.ConfigError, "release.commit is for projects in dependencies/"):
            self.load("member")

    def test_no_tags_needs_a_pinned_commit(self):
        self.project("member", "upstream: { git: https://example.org/m.git, tag_format: null }\nsource: git\n")
        with self.assertRaisesRegex(project_config.ConfigError, "tag_format null .* needs release.commit"):
            self.load("member")

    def test_an_untagged_version_must_name_the_pinned_commit(self):
        config = self.load(self.dependency())
        for version in ("2.0.0+git20260507.4b03484", "2.0.0~rc1+git20260507.4b03484", "0+git20260507.4b03484+dfsg1"):
            with self.subTest(version=version):
                self.assertEqual(project_config.pinned_commit("dep", config, version), self.COMMIT)
        for version, error in [("2.0.0", "isn't <base>\\+git<YYYYMMDD>.<sha>"),
                               ("2.0.0+20260507", "isn't"),
                               ("2.0.0+git2026057.4b03484", "isn't"),
                               ("2.0.0+git20260507.1234567", "names commit 1234567, but release.commit is 4b03484")]:
            with self.subTest(version=version), self.assertRaisesRegex(project_config.ConfigError, error):
                project_config.pinned_commit("dep", config, version)

    def test_an_untagged_release_has_no_tag_and_publishes_to_release(self):
        config = self.load(self.dependency())
        self.assertEqual(project_config.release_tag(config, "2.0.0+git20260507.4b03484"), "")
        self.assertEqual(project_config.channel(config, "2.0.0+git20260507.4b03484"), "release")

    def test_a_tagged_dependency_keeps_its_tag(self):
        config = self.load(self.project("dep", f"""
            upstream: {{ git: https://example.org/dep.git }}
            source: git
            release: {{ commit: {self.COMMIT} }}
            """, directory="dependencies"))
        self.assertEqual(project_config.release_tag(config, "4.15.0"), "v4.15.0")
        # Any version goes: fetch-source checks the tag points at the commit
        self.assertEqual(project_config.pinned_commit("dep", config, "4.15.0"), self.COMMIT)

    def test_a_member_can_build_depend_on_a_dependency(self):
        self.dependency()
        self.project("member", "source: uscan\nbuild_depends: { projects: [dep] }\n")
        self.load("member")
        self.assertEqual(project_config.build_order(["member", "dep"], self.root), ["dep", "member"])


if __name__ == "__main__":
    unittest.main()
