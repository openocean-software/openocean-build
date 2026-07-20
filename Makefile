# Builds the Debian packages locally in Docker. Intended for
# local testing only -- resulting packages are left unsigned (-uc -us).
#
# All commands require OOS_PACKAGE to be set, e.g. "export OOS_PACKAGE=moos-ivp"
#
#   make debian                          # quick build: throwaway container,
#                                        # deps via mk-build-deps; drops to a
#                                        # shell on failure
#   make debian-sbuild                   # full build: sbuild in a schroot
#                                        # chroot (closer to a real
#                                        # archive/buildd build)
#   make debian DISTRO=ubuntu            # same, but against Ubuntu instead
#                                        # of Debian (default codename below)
#   make debian DISTRO=ubuntu DIST=jammy # override the Ubuntu codename
#   make sbuild ARCH=arm64               # cross-arch (needs
#                                        # qemu-user-static on the host)
#   make clean                           # remove fetched source / build
#                                        # products
#   make distclean                       # clean + remove the Docker images
#
# Output .deb, etc. land in build/<package>/<distro>-<dist>-simple (for
# "debian") or build/<package>/<distro>-<dist>-sbuild (for "debian-sbuild").

ifndef OOS_PACKAGE
$(error OOS_PACKAGE is not set. Please run: export OOS_PACKAGE=<package name>)
endif

# DISTRO selects the .deb-based distribution family to build against; it is
# also the Docker Hub repository the base image is pulled from (debian/ubuntu).
DISTRO        ?= debian

ifeq ($(DISTRO),debian)
# Debian has a single rolling target, so take it from debian/changelog.
DIST          := $(shell dpkg-parsechangelog -l $(OOS_PACKAGE)/debian/changelog -S Distribution)
else ifeq ($(DISTRO),ubuntu)
# Ubuntu has no equivalent field (the changelog's Distribution is always a
# Debian codename), so default to the current LTS and let it be overridden.
DIST          ?= resolute
else
$(error Unsupported DISTRO '$(DISTRO)'. Supported values: debian, ubuntu)
endif

DEB_VERSION   := $(shell dpkg-parsechangelog -l $(OOS_PACKAGE)/debian/changelog -S Version)
UPSTREAM_VERSION := $(shell echo $(DEB_VERSION) | sed -E 's/-[^-]+$$//')

ARCH          := amd64

# Passed into the sbuild image so its "builder" user's ids match the
# invoking host user -- see docker/debian/sbuild/Dockerfile.
UID           := $(shell id -u)
GID           := $(shell id -g)

BUILD_DIR     := $(CURDIR)/build/$(OOS_PACKAGE)

SRC_DIR       := $(BUILD_DIR)/$(OOS_PACKAGE)-$(UPSTREAM_VERSION)
ORIG_TARBALL  := $(BUILD_DIR)/$(OOS_PACKAGE)_$(UPSTREAM_VERSION).orig.tar.gz
DSC_FILE      := $(BUILD_DIR)/$(OOS_PACKAGE)_$(DEB_VERSION).dsc

DEBIAN_SBUILD_DOCKER_IMAGE := $(OOS_PACKAGE)-sbuild:$(DISTRO)-$(DIST)
DEBIAN_SBUILD_DOCKER_DIR   := docker/debian/sbuild
DEBIAN_SBUILD_CHROOT       := $(DIST)-$(ARCH)-sbuild

DEBIAN_DOCKER_IMAGE := $(OOS_PACKAGE)-simple:$(DISTRO)-$(DIST)
DEBIAN_DOCKER_DIR   := docker/debian/simple
# dpkg-buildpackage drops its .deb/.changes/.buildinfo directly into the
# parent of the source directory it's invoked from, so this scratch dir
# doubles as the final output location -- no separate move step needed.
DEBIAN_BUILD_DIR    := $(BUILD_DIR)/$(DISTRO)-$(DIST)-simple
DEBIAN_SRC_DIR      := $(DEBIAN_BUILD_DIR)/$(OOS_PACKAGE)-$(UPSTREAM_VERSION)

DEBIAN_SBUILD_OUTPUT_DIR := $(BUILD_DIR)/$(DISTRO)-$(DIST)-sbuild

.PHONY: debian debian-docker-image debian-sbuild debian-sbuild-docker-image source clean distclean

# Quick local build: installs build-deps with mk-build-deps and runs
# dpkg-buildpackage directly in a throwaway container (no chroot-in-a-chroot,
# so it's faster than "sbuild" but less isolated/faithful). If the
# build fails, drops into an interactive shell in the same container --
# source tree, half-installed deps and all -- so you can poke around and
# retry commands by hand. Exit the shell to end the (still-failed) build.
debian: debian-docker-image $(SRC_DIR)
	rm -rf $(DEBIAN_BUILD_DIR)
	mkdir -p $(DEBIAN_BUILD_DIR)
	rm -rf $(SRC_DIR)/debian
	cp -a $(OOS_PACKAGE)/debian $(SRC_DIR)/
	cp -a $(SRC_DIR) $(DEBIAN_SRC_DIR)
	docker run --rm -it \
		-v $(DEBIAN_BUILD_DIR):/build \
		-w /build/$(notdir $(DEBIAN_SRC_DIR)) \
		$(DEBIAN_DOCKER_IMAGE) \
		bash -c '\
			set -x; \
			apt-get -y update; \
			mk-build-deps -i -r -t "apt-get -y -o Debug::pkgProblemResolver=yes --no-install-recommends" debian/control && \
			su builder -c "set -x; dpkg-buildpackage -us -uc -b"; \
			status=$$?; \
			set +x; \
			if [ $$status -ne 0 ]; then \
				echo; \
				echo "=== Build failed (exit $$status). Dropping into a shell to debug -- CTRL-D to end the build. ==="; \
				echo; \
				bash; \
			fi; \
			chown -R $(UID):$(GID) /build; \
			exit $$status'
	@echo "Packages built in $(DEBIAN_BUILD_DIR)"

debian-docker-image: $(DEBIAN_DOCKER_DIR)/Dockerfile
	docker build --build-arg DISTRO=$(DISTRO) --build-arg DIST=$(DIST) \
		--build-arg UID=$(UID) --build-arg GID=$(GID) \
		-t $(DEBIAN_DOCKER_IMAGE) $(DEBIAN_DOCKER_DIR)

# Full build via sbuild in a schroot chroot -- slower (bootstraps a
# minimal chroot, resolves deps with dose3) but closer to how a real
# archive/buildd would build the package.
debian-sbuild: debian-sbuild-docker-image $(DSC_FILE)
	mkdir -p $(DEBIAN_SBUILD_OUTPUT_DIR)
	docker run --rm --privileged \
		-v $(BUILD_DIR):/build \
		-v $(DEBIAN_SBUILD_OUTPUT_DIR):/output \
		-w /output \
		$(DEBIAN_SBUILD_DOCKER_IMAGE) \
		--verbose \
		--chroot=$(DEBIAN_SBUILD_CHROOT) \
		--dist=$(DIST) \
		--arch=$(ARCH) \
		--no-run-lintian --no-run-piuparts --no-run-autopkgtest \
		--no-clean-source \
		--debbuildopt=-uc --debbuildopt=-us \
		/build/$(notdir $(DSC_FILE))
	@echo "Packages built in $(DEBIAN_SBUILD_OUTPUT_DIR)"

# Rebuilds are cheap: docker build no-ops on cache hits once the chroot
# tarball layer exists, so this is safe to run as a prerequisite every time.
debian-sbuild-docker-image: $(DEBIAN_SBUILD_DOCKER_DIR)/Dockerfile
	docker build --build-arg DISTRO=$(DISTRO) --build-arg DIST=$(DIST) --build-arg ARCH=$(ARCH) \
		--build-arg UID=$(UID) --build-arg GID=$(GID) \
		-t $(DEBIAN_SBUILD_DOCKER_IMAGE) $(DEBIAN_SBUILD_DOCKER_DIR)

# Fetches the upstream release matching debian/watch and repacks it as the
# .orig tarball expected by dpkg-source, applying debian/copyright's
# Files-Excluded (strips the bundled non-DFSG-free proj-5.2.0 copy).
source: $(ORIG_TARBALL)

$(ORIG_TARBALL):
	mkdir -p $(BUILD_DIR)
	uscan --destdir=$(BUILD_DIR) --verbose $(OOS_PACKAGE)

# uscan only produces the orig tarball; unpack it into the source
# directory dpkg-source expects. The extracted top-level directory name
# (from the upstream tag) doesn't necessarily match $(SRC_DIR) exactly
# (e.g. it lacks the +dfsg suffix), so move it into place explicitly.
$(SRC_DIR): $(ORIG_TARBALL)
	rm -rf $(SRC_DIR) $(BUILD_DIR)/.extract
	mkdir -p $(BUILD_DIR)/.extract
	tar -xzf $(ORIG_TARBALL) -C $(BUILD_DIR)/.extract
	mv $(BUILD_DIR)/.extract/* $(SRC_DIR)
	rmdir $(BUILD_DIR)/.extract

$(DSC_FILE): $(SRC_DIR) $(shell find $(OOS_PACKAGE)/debian -type f)
	rm -rf $(SRC_DIR)/debian
	cp -a $(OOS_PACKAGE)/debian $(SRC_DIR)/
	cd $(BUILD_DIR) && dpkg-source -b $(notdir $(SRC_DIR))

clean:
	rm -rf $(BUILD_DIR)

distclean: clean
	-docker rmi $(DEBIAN_SBUILD_DOCKER_IMAGE) $(DEBIAN_DOCKER_IMAGE)
