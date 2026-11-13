# Projects

*This documentation section was written by Claude.*

| Project | Upstream | Source | Releases | Continuous |
|---|---|---|---|---|
| moos-ivp | [moos-ivp/moos-ivp](https://github.com/moos-ivp/moos-ivp) | uscan | release | no |
| openocean-messages | [openocean-software/openocean-messages](https://github.com/openocean-software/openocean-messages) | git, signed tags | release, beta (`v*-alpha.N`, `-beta.N`, `-rc.N`) | on each push |
| dccl | [GobySoft/dccl](https://github.com/GobySoft/dccl) | git, signed tags | release, beta (`*_alphaN`, `_betaN`, `_rcN`) | on each push |
| moos-ivp-helm | [GobySoft/moos-ivp](https://github.com/GobySoft/moos-ivp), branch `ivp-without-moos` | git | none: snapshots only | every 6 hours |
| goby3 | [GobySoft/goby3](https://github.com/GobySoft/goby3) | git, signed tags | release | on each push |

## Adding a project

Each project is a directory in `projects/`, named after its Debian source package:

- `project.yaml`: where the source comes from, and any settings that differ from `defaults.yaml`.
- `debian/`: the Debian packaging. Its `changelog` version is what gets released, with `trixie` as the distribution.
- `maintainers.yaml` and `keys/`: the people whose signatures are trusted for this project (see [Signing and trust](trust.md)).

A project whose packages build-depend on another project's, or on an external repository's, lists them under `build_depends` (see [Build dependencies](build-dependencies.md)). Software a member project needs that isn't packaged elsewhere is a [dependency project](dependencies.md), in `dependencies/`.

`moos-ivp-helm` is the IvP helm libraries built without MOOS (`IVP_BUILD_HELM_LIBS_ONLY`, from MOOS-IvP master with [moos-ivp/moos-ivp#147](https://github.com/moos-ivp/moos-ivp/pull/147)), installed under `/usr/lib/<multiarch>/ivp-helm` so they coexist with the `libmoos-ivp` packages; `find_package(IVP CONFIG)` finds them with that prefix in `CMAKE_PREFIX_PATH`. The branch has no tags of its own, so the project is snapshots only.

A `project.yaml` for a project with signed tags `v1.2.0` and prereleases `v1.3.0-rc.1`:

```yaml
upstream:
  git: https://github.com/example/example.git
  # tag_format: "v{version}"      # default
  # prerelease_separator: "-"     # default; Debian "~" becomes this in the tag

source: git
release:
  require_signed_tag: true
  beta_tags: '^v[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.[0-9]+$'

continuous:
  enabled: true

targets:
  # arch_all: amd64                    # build Architecture: all packages on amd64 only
  exclude:
    - { distro: ubuntu, dist: jammy }   # if a dependency is too old there

install_test:
  packages: [cmake, g++, make]
  commands:
    - example --version
```

A project that only builds snapshots of a branch sets `release.enabled: false` (a changelog change releases nothing) and `continuous.enabled: true`, with `continuous.branch` if the branch isn't the repository's default. Its pull request checks build the branch head instead of the changelog version. Versions come from the last tag reachable from the head; where none is (a fork without tags, or a branch upstream tags elsewhere), `continuous.version_base` names the release the snapshots follow.

`ci/project-config <project> get <setting>` shows the merged settings, and `ci/project-config <project> matrix` the build targets. To build locally, see the top of the `Makefile`:

```bash
make debian-sbuild OOS_PACKAGE=example DISTRO=ubuntu DIST=resolute
```

A pull request that adds or changes a project builds and install-tests it on Ubuntu 26.04 (amd64). Once merged, the new `debian/changelog` releases it (after approval), and continuous builds start with the next poll or push.
