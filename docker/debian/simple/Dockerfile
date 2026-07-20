# Throwaway container for "make debian": a plain dpkg-buildpackage build,
# with build-deps resolved at container run time (via mk-build-deps)
# rather than baked into the image, so debian/control changes take effect
# without an image rebuild.
#
# DISTRO selects the Docker Hub repository (debian or ubuntu); DIST is the
# codename within it (e.g. trixie, resolute).
ARG DISTRO=debian
ARG DIST=trixie
FROM ${DISTRO}:${DIST}

RUN apt-get update && \
    apt-get -y --no-install-recommends install \
        build-essential dpkg-dev devscripts equivs fakeroot \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Matched to the host user (see the Makefile) so dpkg-buildpackage -- run as
# this user, not root, by the "debian" Makefile target -- leaves build
# artifacts owned by the invoking user instead of root. Ubuntu's base image
# (unlike Debian's) already ships a UID/GID-1000 "ubuntu" user, so drop
# whatever already holds these ids first to make sure "builder" reliably
# lands on them.
ARG UID=1000
ARG GID=1000
RUN existing_user="$(getent passwd "${UID}" | cut -d: -f1)"; \
        [ -n "$existing_user" ] && userdel -r "$existing_user" 2>/dev/null; \
    existing_group="$(getent group "${GID}" | cut -d: -f1)"; \
        [ -n "$existing_group" ] && groupdel "$existing_group" 2>/dev/null; \
    groupadd -g "${GID}" builder && \
    useradd -m -s /bin/bash -u "${UID}" -g "${GID}" builder

WORKDIR /build
