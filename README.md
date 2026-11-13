# openocean-build

This repository contains build metadata for our [member projects](https://oceansoft.org/member-projects/).

Member projects are responsible for maintaining source code using git, a working build and install (CMake for C/C++, pyproject.toml for Python, etc.), and a tagged release schedule. From there, Open Ocean Software handles the secure build pipeline and package repository.

![](docs/figures/high-level-build.png)

We are working to support multiple distribution ecosystems, based on a tiered approach (Tier 1 is highest priority).


## Tier 1

- Debian and Ubuntu (*.deb): These Linux distributions are widely used in the marine robotics community, and have a vast collection of existing packages.

## Tier 2

- OpenEmbedded (Yocto): used by many embedded developers to create bespoke filesystems from source.
- Homebrew: supports MacOS X and can be used on many Linux distros as well (independent of the system package manager).

## Documentation

*This section was written by Claude.*

The documentation, published at [build.oceansoft.org](https://build.oceansoft.org) (source in [docs/](docs/index.md)), covers installing packages, the release, beta and continuous channels, signing and trust, keys, continuous builds and adding a project. Build it with `mkdocs serve` (needs `mkdocs` and `mkdocs-material`).

For the release channel on Debian trixie, save [`keys/release.asc`](keys/release.asc) (fingerprint `EAD2 78F6 A256 44C2 C559  DBB6 D28A 2EAB 14D2 A9E5`) as `/usr/share/keyrings/openocean-release.asc`, and add `/etc/apt/sources.list.d/openocean.sources`:

```
Types: deb
URIs: https://apt.oceansoft.org/release/debian-trixie
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/openocean-release.asc
```
