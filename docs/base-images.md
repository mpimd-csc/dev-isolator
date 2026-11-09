## Base images

This section explains the base image level, on which every project image is
built; the project level is described in
[Project configuration](project-configuration.md#project-configuration).

### What a project `Containerfile`'s `FROM` references

The single `FROM` instruction of a project's `Containerfile` must reference a
base image dev-isolator can build the dev-isolator base image from. The
simplest form is a plain base name matching one of the bundled base images
(see [Bundle base images and addons](base-images.md#bundle-base-images-and-addons)):

```dockerfile
FROM ubuntu:26.04
```

This is what the `Containerfile` written by `init` uses by default. dev-isolator
resolves the name to the matching bundled base Containerfile (see
[below](base-images.md#where-a-base-image-comes-from)) and builds the dev-isolator base image
from it.

Any other image works as well, as long as one of the supported package managers
(`apt`, `dnf`, `zypper` or `pacman`) is available in it: dev-isolator runs the
image in a short-lived container to detect the package manager, and builds the
base image from the bundled Containerfile of that package manager. The `FROM`
image must carry a tag; a podman error is raised when the image cannot be run,
and dev-isolator aborts with `RuntimeError: Package manager ... not supported.`
when none of the supported package managers is found.

### Where a base image comes from

Base images are built from the Containerfiles bundled with dev-isolator
(`data/base/Containerfile.<name>`, for example `Containerfile.ubuntu-26.04`).
Each file's first comment block declares the base images it covers (several
aliases can share one file, e.g. `ubuntu:24.04` and `ubuntu:noble`) and the
package manager it provides:

```dockerfile
# DI_BASE = ubuntu:26.04
# DI_BASE = ubuntu:resolute
# DI_PKG = apt
FROM ubuntu:26.04
RUN apt-get update && apt install -y cmake build-essential
```

The `DI_PKG` comment names the package manager of the base; there is one
bundled base per supported package manager (`apt`, `dnf`, `zypper` and
`pacman` — Ubuntu, Debian, Fedora, AlmaLinux, openSUSE, Arch, …). When a
project `Containerfile`'s `FROM` image is not one of the `DI_BASE` names, the
package manager detected in that image (see
[above](base-images.md#what-a-project-containerfiles-from-references)) selects the bundled
base Containerfile.

The built base image is tagged `dev-isolator-<name>:<tag>-<hash>`, where
`name` and `tag` are the parts of the project `Containerfile`'s `FROM` image
around its `:` — so a `FROM ubuntu:26.04` produces
`dev-isolator-ubuntu:26.04-<hash>` (podman shows the image with a
`localhost/` registry prefix in the local image store, e.g.
`localhost/dev-isolator-ubuntu:26.04-<hash>`).
The hash is the SHA-256 hash of the merged base Containerfile content (see
[below](base-images.md#what-goes-into-the-base-image-what-goes-into-the-project-image)), so
rebuilds with a different setup get their own tag. The project image build
rewrites the `FROM` instruction of your `Containerfile` to this hashed tag,
so it always references the base image matching the current setup.

### What goes into the base image, what goes into the project image

The base image keeps everything *shared* by all projects (and tag variants) of a
machine:

- the base setup of the bundled base Containerfile,
- the host user block: your host UID, GID and username are set up (removing any
  pre-existing user/group occupying those properties),
- the enabled addons rendered into container instructions (`addon.<name>.jinja2`
  files; see [Project configuration](project-configuration.md#project-configuration) for how the addons
  are enabled).

Your project contributes its own steps in the project-level build: the contents of
the project `Containerfile` (system packages etc.) plus the trailing workdir
switch, `USER <you>` and `WORKDIR /workspace`.

Because addons and the host user block are baked into the base image, changing
`config.yaml` (or your addons) triggers a rebuild of the base image on the next
run; the project `Containerfile` does not need the host user steps anymore.

### Base image cleanup

`clean --all` only removes images whose repository is exactly `dev-isolator` (the
project images) — see [clean](usage.md#clean). The base images are named
`dev-isolator-<name>:<tag>-<hash>` and are deliberately not removed by `clean`:
being shared by all projects, they stay installed until you remove them manually
with `podman rmi dev-isolator-ubuntu:26.04-<hash>` (the hash can be found with
`podman images`, or with `--verbose`/`-vvv` during a build).

### Bundle base images and addons

- Base images:
  - `ubuntu:22.04`, `ubuntu:24.04`, `ubuntu:26.04` (Ubuntu)
  - `debian:13` (Debian, trixie)
  - `fedora:43`, `fedora:44`, `fedora:45`, `fedora:rawhide` (Fedora)
  - `almalinux:9`, `almalinux:10` (AlmaLinux / RHEL)
  - `opensuse-leap:16.0` (openSUSE Leap)
  - `opensuse-tumbleweed:latest` (openSUSE Tumbleweed)
  - `archlinux:latest` (Arch Linux)
- Addons:
  - `opencode`, installs the `opencode` CLI tool
  - `claude`, installs the `claude` CLI tool