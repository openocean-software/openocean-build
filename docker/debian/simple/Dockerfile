# Throwaway container for "make debian": a plain dpkg-buildpackage build,
# with build-deps resolved at container run time (via mk-build-deps)
# rather than baked into the image, so debian/control changes take effect
# without an image rebuild.
ARG DIST=trixie
FROM debian:${DIST}

RUN apt-get update && \
    apt-get -y --no-install-recommends install \
        build-essential dpkg-dev devscripts equivs fakeroot \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
