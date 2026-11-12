# Installing packages

*This documentation section was written by Claude.*

Packages are published per [channel](channels.md) at `https://apt.oceansoft.org/<channel>/<distro>-<dist>`, for:

| Distribution | Releases (`<distro>-<dist>`) | Architectures |
|---|---|---|
| Debian | `debian-bookworm`, `debian-trixie` | amd64, arm64 |
| Ubuntu | `ubuntu-jammy`, `ubuntu-noble`, `ubuntu-resolute` | amd64, arm64 |

Some projects skip a release whose dependencies are too old (see each project's `project.yaml`).

## With the setup script

[`scripts/apt-setup`](https://github.com/openocean-software/openocean-build/blob/main/scripts/apt-setup) adds the channels, checking each key's fingerprint first:

```bash
curl -fsSLO https://raw.githubusercontent.com/openocean-software/openocean-build/main/scripts/apt-setup
```

```bash
sudo bash apt-setup                 # the release channel
```

```bash
sudo bash apt-setup --beta --zenoh  # also the beta channel, and Eclipse Zenoh's repository
```

`--continuous` adds the continuous channel. `--zenoh` is for packages that use Zenoh, such as `goby3-zenoh`: it adds Eclipse's repository pinned to the Zenoh version our packages are built against, since `libzenohc` has no versioned SONAME to tell an incompatible version apart.

The sections below do the same by hand.

## Release channel

Save [`keys/release.asc`](https://github.com/openocean-software/openocean-build/blob/main/keys/release.asc) as `/usr/share/keyrings/openocean-release.asc`, then add `/etc/apt/sources.list.d/openocean.sources` (here for Debian trixie):

```
Types: deb
URIs: https://apt.oceansoft.org/release/debian-trixie
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/openocean-release.asc
```

## Beta channel

For prereleases, add the beta channel as well. It's signed with the same key:

```
Types: deb
URIs: https://apt.oceansoft.org/beta/debian-trixie
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/openocean-release.asc
```

A prerelease such as `0.1.0~beta.1` sorts below `0.1.0`, so the final release replaces it once published.

## Continuous channel

Save [`keys/continuous.asc`](https://github.com/openocean-software/openocean-build/blob/main/keys/continuous.asc) as `/usr/share/keyrings/openocean-continuous.asc`:

```
Types: deb
URIs: https://apt.oceansoft.org/continuous/debian-trixie
Suites: trixie
Components: main
Signed-By: /usr/share/keyrings/openocean-continuous.asc
```

The continuous channel can be used alone, or alongside the release channel: a continuous build sorts above the release it follows, so apt picks it.

The key fingerprints are listed in [Keys](keys.md); check them before trusting a downloaded key:

```bash
gpg --show-keys /usr/share/keyrings/openocean-release.asc
```
