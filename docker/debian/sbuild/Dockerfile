# Local sbuild environment for building the moos-ivp Debian packages.
# Not for production/signed builds -- see the "debian" Makefile target.
#
# Uses classic schroot-backed sbuild rather than the newer "unshare"
# chroot mode: unshare needs the (non-root) build user to create a
# nested user namespace, which Docker's --privileged does not actually
# grant to a non-root process. schroot's setuid-root helper does the
# privileged chroot()/mount() work instead, which works fine here.
#
# DISTRO selects the Docker Hub repository (debian or ubuntu) for both the
# outer container and the bootstrapped chroot; DIST is the codename within
# it (e.g. trixie, resolute).
ARG DISTRO=debian
ARG DIST=trixie
FROM ${DISTRO}:${DIST}

ARG DISTRO
ARG DIST
ARG ARCH=amd64

# Matched to the host user (see the Makefile) so files "builder" writes to
# bind-mounted host directories (e.g. /output) are writable/removable by
# the invoking user rather than left owned by some arbitrary container UID.
ARG UID=1000
ARG GID=1000

RUN apt-get update && \
    apt-get -y --no-install-recommends install \
        sbuild schroot mmdebstrap uidmap \
        dpkg-dev devscripts eatmydata ccache \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build the sbuild chroot once, at image build time, so the "debian"
# target doesn't have to re-bootstrap a chroot on every run.
#
# Ubuntu splits build-essential packages across main and universe (Debian
# keeps them all in main), so universe is added for that case.
RUN case "${DISTRO}" in \
        debian) MIRROR=http://deb.debian.org/debian; COMPONENTS=main ;; \
        ubuntu) MIRROR=http://archive.ubuntu.com/ubuntu; COMPONENTS=main,universe ;; \
        *) echo "Unsupported DISTRO '${DISTRO}'" >&2; exit 1 ;; \
    esac; \
    mkdir -p /srv/chroot/${DIST}-${ARCH} && \
    mmdebstrap --arch=${ARCH} --variant=buildd --components="${COMPONENTS}" "${DIST}" \
        /srv/chroot/${DIST}-${ARCH} \
        "${MIRROR}"

RUN printf '[%s-%s-sbuild]\ntype=directory\ndirectory=/srv/chroot/%s-%s\nprofile=sbuild\ngroups=sbuild\nroot-groups=sbuild\nunion-type=none\n' \
        "${DIST}" "${ARCH}" "${DIST}" "${ARCH}" \
        > /etc/schroot/chroot.d/${DIST}-${ARCH}-sbuild

# Ubuntu's sbuild package doesn't ship the sbuild-adduser wrapper that
# Debian's does, nor does its postinst create the "sbuild" group (Debian's
# does), so create the group if needed (-f is a no-op if it already exists)
# and add the membership by hand instead.
#
# Ubuntu's base image (unlike Debian's) already ships a UID/GID-1000 "ubuntu"
# user, which would otherwise push "builder" onto the next free UID and
# silently break bind-mount permissions whenever that no longer lines up
# with the host user. Drop whatever already holds UID/GID first so "builder"
# reliably lands on the requested ids on both distros.
RUN existing_user="$(getent passwd "${UID}" | cut -d: -f1)"; \
        [ -n "$existing_user" ] && userdel -r "$existing_user" 2>/dev/null; \
    existing_group="$(getent group "${GID}" | cut -d: -f1)"; \
        [ -n "$existing_group" ] && groupdel "$existing_group" 2>/dev/null; \
    groupadd -g "${GID}" builder && \
    useradd -m -s /bin/bash -u "${UID}" -g "${GID}" builder && \
    groupadd -f sbuild && \
    usermod -aG sbuild builder

USER builder
WORKDIR /build

ENTRYPOINT ["sbuild"]
