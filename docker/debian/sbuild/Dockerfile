# Local sbuild environment for building the moos-ivp Debian packages.
# Not for production/signed builds -- see the "debian" Makefile target.
#
# Uses classic schroot-backed sbuild rather than the newer "unshare"
# chroot mode: unshare needs the (non-root) build user to create a
# nested user namespace, which Docker's --privileged does not actually
# grant to a non-root process. schroot's setuid-root helper does the
# privileged chroot()/mount() work instead, which works fine here.
ARG DIST=trixie
FROM debian:${DIST}

ARG DIST
ARG ARCH=amd64

RUN apt-get update && \
    apt-get -y --no-install-recommends install \
        sbuild schroot mmdebstrap uidmap \
        dpkg-dev devscripts eatmydata ccache \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Build the sbuild chroot once, at image build time, so the "debian"
# target doesn't have to re-bootstrap a chroot on every run.
RUN mkdir -p /srv/chroot/${DIST}-${ARCH} && \
    mmdebstrap --arch=${ARCH} --variant=buildd "${DIST}" \
        /srv/chroot/${DIST}-${ARCH} \
        http://deb.debian.org/debian

RUN printf '[%s-%s-sbuild]\ntype=directory\ndirectory=/srv/chroot/%s-%s\nprofile=sbuild\ngroups=sbuild\nroot-groups=sbuild\nunion-type=none\n' \
        "${DIST}" "${ARCH}" "${DIST}" "${ARCH}" \
        > /etc/schroot/chroot.d/${DIST}-${ARCH}-sbuild

RUN useradd -m -s /bin/bash builder && sbuild-adduser builder

USER builder
WORKDIR /build

ENTRYPOINT ["sbuild"]
