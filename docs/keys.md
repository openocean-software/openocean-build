# Keys

*This documentation section was written by Claude.*

| Key | Fingerprint | Expires | Public key | Used for |
|---|---|---|---|---|
| Release (primary) | `EAD2 78F6 A256 44C2 C559  DBB6 D28A 2EAB 14D2 A9E5` | | `keys/release.asc` | Certifies the release signing subkey |
| Release (signing subkey) | `D7D7 224D 53A4 F29E 303F  E01D EB50 5B68 A5D0 63B0` | 2028-01-01 | `keys/release.asc` | Signs the release and beta repositories |
| Continuous | `DAF0 8DD7 788E 6C84 C6F8  E8D3 E59E 1A81 FECD B228` | 2028-11-02 | `keys/continuous.asc` | Signs the continuous repositories |
| GitHub web-flow | `9684 79A1 AFF9 27E3 7D1A  566B B569 0EEE BB95 2194` | | `trust/github-web-flow.asc` | Verifying web-UI merges for continuous builds |
| Maintainers | listed in each `maintainers.yaml` | | `projects/<p>/keys/` | Verifying release tags and continuous heads |

## Release key

The release and beta channels are signed by a subkey of the Open Ocean Software release key. Only the subkey's private half is stored in GitHub, as the `GPG_PRIVATE_KEY` (and `GPG_PASSPHRASE`) secrets of the `release` Environment; the primary key stays offline. If the subkey is compromised it can be revoked and replaced from the primary key, and users who trust the primary key's fingerprint need only fetch the updated `keys/release.asc`.

## Continuous key

The continuous channel is signed by a separate key, held in the `continuous` Environment's secrets. Continuous builds run without a reviewer, so a separate key means nothing built from an unreviewed head can be signed as a release.

## GitHub web-flow key

GitHub signs commits made in its web UI (including pull request merges) with this key. It's only trusted together with the pull request's merger, as described in [Signing and trust](trust.md#continuous-builds-signed-or-merged-heads).

## Maintainer keys

Each project's `maintainers.yaml` lists its maintainers' GPG fingerprints (or SSH public keys), and `projects/<p>/keys/*.asc` holds the GPG public keys. See [Signing and trust](trust.md).

## Rotating and extending keys

*Outline for the maintainers to fill in:*

- Extending a key's expiry: where the offline primary key is kept, the `gpg --quick-set-expire` steps, and re-exporting `keys/release.asc` (subkey only).
- Replacing the release signing subkey: generating it, updating `signing_key` in `defaults.yaml` and the `release` Environment secrets, and announcing it.
- Replacing the continuous key.
- Refreshing `trust/github-web-flow.asc` if GitHub rotates its key.
- Adding or removing a maintainer (a pull request changing `maintainers.yaml` and `keys/`, reviewed per CODEOWNERS).
