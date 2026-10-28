# dev-isolator

`dev-isolator` is a command line tool that manages a per-project,
[podman](https://podman.io/) based development environment.

**Requirements:**

- Linux with a working podman installation.
- Python 3.11 or later.
- Python dependecies:
  - [`podman`](https://podman-py.readthedocs.io/en/latest/)
  - [`pyyaml`](https://pypi.org/project/PyYAML/)
  - [`jinja2`](https://jinja.palletsprojects.com/en/stable/)
  - [`tree-sitter-containerfile`](https://github.com/wharflab/tree-sitter-containerfile/)
  - [`pytest`](https://docs.pytest.org/en/stable/), only used by the test-suite (but currently installed with dev-isolator as a regular dependency)

Both commands `dev-isolator` and `di` (a short alias) start the tool.

## Installation

Before using `dev-isolator` [podman](https://podman.io/) must be enabled as 
user service. Typically, this is done by executing 
```shell
systemctl --user enable --now podman.service
```
in a terminal. Afterwards, one can proceed with the installation of 
`dev-isolator`.


Install from a source checkout for development:

```bash
pip install -e .
```

Run the test suite with:

```bash
pytest
```

## Usage


### Command line overview

```
dev-isolator [--verbose/-v]... [--shell/-s <shell>] [--config/-c <name>] <subcommand> [arguments]
```

**Global options:**

| Option | Description |
|---|---|
| `--verbose`, `-v` | Increase verbosity. Repeat for more details: `-v` (info), `-vv` (debug), `-vvv` (trace, e.g. prints the merged Containerfile and build logs). |
| `--shell`, `-s` | Shell to start inside the container. Default: `bash`. Applies to `shell` and to an interactive session started by `run` when no command is given. |
| `--config`, `-c` | Name of a sub-configuration: a subdirectory of `.devisolator/` holding its own `Containerfile` and `config.yaml` (see [Sub-configurations (`--config`)](#sub-configurations---config)). An explicit `--config` takes precedence over the `DEVISOLATOR_CONFIG` environment variable. |

**Subcommands:**

| Subcommand | Description |
|---|---|
| `build` | Build the image of the current project. |
| `shell` | Open an interactive shell inside the container. |
| `run <command> [args...]` | Run an arbitrary command inside the container. |
| `opencode [args...]` | Run [opencode](https://opencode.ai/) inside the container. |
| `claude [args...]` | Run [claude](https://claude.ai/) inside the container. |
| `clean` | Remove dev-isolator images. |

Without an explicit subcommand, `shell` is executed with the default
shell (`bash`).

`opencode`, `claude` and `run` pass all following (unknown) arguments
through to the command inside the container; their own options are not
parsed by `dev-isolator`.

### `build`

```bash
dev-isolator build
```

Builds the image of the current project:

- The base image referenced by the project's `Containerfile` is built
  first; this happens on every invocation, but the build cache keeps
  it cheap when nothing changed. The base image is built from a
  Containerfile bundled with dev-isolator, merging the base setup, the
  appended host user block and the enabled addon templates, and it is
  tagged `dev-isolator-<name>:<tag>` (see
  [Base images](#base-images)). The base image is shared, so it is
  only built once per base setup; a project `Containerfile` change
  does not affect it, but addon or `config.yaml` changes rebuild it.
- The project's `Containerfile` (from `<project>/.devisolator/`)
  must be based on that base image and is built from the build
  context (the directory containing the `.devisolator` directory).
  The build always ends with `USER <you>` and
  `WORKDIR /workspace`.
- If a previous image carried the same tag but the build produced a
  different image id, the superseded image is removed so no dangling
  image is left behind.

Building aborts with an error when the `Containerfile` does not use a
dev-isolator base image, or when the referenced base image is not
bundled with dev-isolator (see
[Base images](#base-images)).

**Options:**

| Option | Description |
|---|---|
| `--nocache`, `-n` | Pass `--nocache` to `podman build`, i.e. ignore the build cache. |
| `--keep-container-file`, `-k` | On a build error, keep the generated (merged) Containerfile in the current directory (`Containerfile-fail-*`) for inspection instead of removing it. |

### `shell`

```bash
dev-isolator shell
dev-isolator              # equivalent, shell is the default
dev-isolator --shell zsh  # start zsh instead of bash
```

Opens an interactive shell inside the container:

1. The image is always built first (as with `build`, without
   `--nocache`), rebuilding is cheap thanks to the build cache.
2. A container named
   `dev-isolator-<slug_of_the_project_path>--<tag>-<8 random characters>`
   is created and started. The
   random suffix lets several containers of the same project run in
   parallel. It
   runs `sleep infinity` as its entrypoint, so it stays alive for the
   session. The container runs with `--userns=keep-id`, i.e. the same
   UID/GID as the host user, so files created in the `/workspace` bind
   mount stay owned by you.
3. The current working directory is mounted read-write at
   `/workspace`, plus the environment variables and volumes declared
   in `config.yaml` and requested by the enabled addons (see
   [Project configuration](#project-configuration) and
   [Addon configuration](#addon-configuration-configaddon-yaml)).
4. Your terminal is attached to a `podman exec -it` session running
   the shell (`bash` by default). Leave the session with `exit` or
   Ctrl-D.
5. After the session ends, the container is stopped and removed.

Options:

See [Command line overview](#command-line-overview) for the global
`--shell`/``-s` option, which selects the shell started here.

### `run`, `opencode`, `claude`

```bash
dev-isolator run make test
dev-isolator opencode --port 4096
dev-isolator claude --model xyz
```

Work exactly like `shell`, except that instead of an interactive shell
the given command is executed inside the container (via
`podman exec -it`) and all following arguments are passed to it
unchanged. The command must exist inside the image; `opencode` and
`claude` are provided by the respective bundled addons (see below). The
exit code of the session is returned, and the container is stopped and
removed afterwards.

### `clean`

```bash
dev-isolator clean
dev-isolator clean --all
dev-isolator clean --local
```

Removes the image of the current project (`clean`), every image whose
repository is `dev-isolator` (`clean --all`), or every dev-isolator
image of the current project, including all its tag variants
(`clean --local`).

The image removed by `clean` is the one of the currently selected
configuration: the project default (`dev-isolator:<slug>--default`),
or — when [`--config`](#sub-configurations---config) is in effect — the
selected sub-configuration's image. `clean --all` and `clean --local`
remove images regardless of the selection.

Before removal, all matching containers are stopped (after 10 seconds
they are killed) and removed. A container matches when it was created
from one of the images or when its name starts with the project's
container name prefix (`dev-isolator-<slug>--<tag>` for `clean`,
`dev-isolator-<slug>` for `clean --local`, or `dev-isolator-` for
`clean --all`). With `--verbose` the removed containers and images
are printed.

Base images (`dev-isolator-<name>:<tag>`, e.g.
`dev-isolator-ubuntu:24.04`) are never removed by `clean`; they are
shared by all projects (see
[Base image cleanup](#base-image-cleanup)).

Options:

| Option | Description |
|---|---|
| `--all`, `-a` | Remove all dev-isolator images instead of only the current one. |
| `--local`, `-l` | Remove every dev-isolator image of the current project (all tag variants) instead of only the current one. Mutually exclusive with `--all`. |

### Discovery of the project directory

The project directory is located by searching for a `.devisolator`
directory starting at the current working directory and walking up the
directory tree (up to the home directory, or the filesystem root for
working directories outside the home tree). The first hit wins.

If no `.devisolator` directory is found, the `Containerfile` and
configuration bundled with dev-isolator are used.

#### `--config`: selecting a sub-configuration

Sub-configurations are selected from this project directory with
`--config <name>` or the `DEVISOLATOR_CONFIG` environment variable;
see [Sub-configurations (`--config`)](#sub-configurations---config).

Hotkeys: none. Detach from an interactive session with Ctrl-D or
`exit` (the detach keys are empty).

### Sub-configurations (`--config`)

Large projects often need more than one container environment, for
example a slim default image for everyday work and a specialized one
for GPU builds or documentation. Sub-configurations solve this without
touching the default setup: each variant lives in its own subdirectory
of `.devisolator/` and is selected per invocation (or per session)
with `--config`.

```bash
dev-isolator --config gpu build    # build the "gpu" sub-configuration
dev-isolator --config gpu shell    # open a shell in it
```

The same selection can be made through the environment variable
`DEVISOLATOR_CONFIG`; it applies whenever `--config` is not given on
the command line, so an explicit `--config` wins:

```bash
export DEVISOLATOR_CONFIG=gpu      # "gpu" for every call in this shell
dev-isolator build                 # gpu, no --config needed
dev-isolator --config base build   # an explicit --config overrides the variable
```

#### What a sub-configuration overrides

For a sub-configuration `<name>`, the `Containerfile` and the
`config.yaml` are looked up in the following order, and the *first
found file wins* (the files are never merged):

1. `.devisolator/<name>/` — the sub-configuration itself.
2. `.devisolator/` — the project's default configuration.
3. The files bundled with dev-isolator.

Because the project defaults sit in the middle of this chain, a
sub-configuration only *overrides* the files it actually contains: a
`gpu/` directory holding just a `Containerfile` reuses the default
`config.yaml`, and a `config.yaml` alone reuses the default
`Containerfile`. If the named directory does not exist at all,
dev-isolator aborts with an error (exit code 255) — sub-configurations
are strictly project-local; the bundled defaults know no
subdirectories.

The name `default` is reserved: `--config default` (or
`DEVISOLATOR_CONFIG=default`) explicitly selects the default
configuration and is equivalent to omitting the option entirely,
including the resulting `--default` image tag. A directory
`.devisolator/default/`, should one exist, is never used as a
sub-configuration.

The enabled addon templates (`addon.*.jinja2`) are *collected* from
all three places (each directory sorted by file name, duplicates
removed), so they can be added per sub-configuration, per project or
come from the bundle. Which of them are enabled — and which
`environment`/`volumes` settings are applied — is decided by the
single active `config.yaml` (first found), exactly as for the default
configuration.

One file type is deliberately *not* part of a sub-configuration: the
per-addon runtime settings (`config.<addon>.yaml`) are always read
from the project's `.devisolator/` directory (first found) or, when
missing there, from the bundled ones. This keeps the runtime
environment/volume wiring shared across all sub-configurations.

#### Naming: every sub-configuration gets its own image

The sub-configuration name doubles as the image tag. Building under
`--config gpu` produces the image
`dev-isolator:<slug_of_the_project>--gpu` (with the default
configuration the tag is `default`), and the container is created as
`dev-isolator-<slug_of_the_project>--gpu`. Several sub-configurations
of a project can therefore be built and kept side by side, and
`build`, `shell`, `run`, `opencode` and `claude` always act on the
currently selected one. [`clean`](#clean) removes the selected
configuration's image; use `clean --local` to drop every tag variant
of the project at once.

#### Example: a project with a `gpu` sub-configuration

```
my-project/
└── .devisolator/
    ├── Containerfile            # based on a dev-isolator base image
    ├── config.yaml              # default addons and settings
    └── gpu/
        ├── Containerfile        # e.g. a CUDA-enabled base image
        └── config.yaml          # e.g. a different addon selection
```

```bash
dev-isolator build                  # builds dev-isolator:<slug>--default
dev-isolator --config gpu build     # builds dev-isolator:<slug>--gpu
dev-isolator --config gpu shell     # shell inside the "gpu" container
dev-isolator clean                  # removes the default image only
dev-isolator clean --local          # removes default *and* gpu images
```

### Project configuration

The configuration of a containerized dev environment lives in the
`.devisolator` directory of your project:

- `Containerfile`: The base image definition.
- `config.yaml`: Configures the addons and the container. Besides the
  enabled addons it can also declare environment variables and
  additional volume mounts for the container:

  ```yaml
  addons:
    - opencode
    - claude
  environment:
    - OMP_NUM_THREADS
    - PYTHONDONTWRITEBYTECODE=1
  volumes:
    - /tmp:/host_tmp
  ```

- `addon.<name>.jinja2`: A jinja2 template with the Containerfile
  instructions appended for the addon `<name>`. It receives the
  variables `uid`, `gid`, `username` and `home_dir` of the invoking
  host user.
- `config.<addon>.yaml`: Runtime settings an addon requests for its
  container (see below).

When a container is started, the `environment` and `volumes` sections
of `config.yaml` are applied (see [below](#project-configuration)),
followed by the settings requested by the enabled addons. Addon
settings override those from `config.yaml`; the mount of the current
directory at `/workspace` always takes precedence.

Configuration resolution rules:

- For `config.yaml` and `config.<addon>.yaml`, strictly the first file
  found is used: the file in `<project>/.devisolator/` if it exists,
  otherwise the one bundled with dev-isolator. Only one is active; the
  two are never merged.
- The `addons` list names the addons to append to the
  Containerfile. Without any `config.yaml` at all, every
  `addon.*.jinja2` file is enabled.
- The `environment` and `volumes` sections of `config.yaml` are
  processed exactly like those of an addon config file (see below):
  `environment` entries are either `NAME` (taken from the host) or
  `NAME=value` (used as given), and `volumes` lists
  `host:container[:mode]` specs.
- Addon templates are collected first from the directory bundled with
  dev-isolator, then from `.devisolator/` (sorted by file name);
  duplicates are removed. They are rendered into the base image's
  Containerfile (see [Base images](#base-images)), not into the
  project one.
- The host user block is likewise appended to the base image's
  Containerfile: a user with the same UID, GID and username as the
  invoking host user is created (removing any pre-existing user/group
  occupying those properties) and made the image's default user. The
  project image build appends only the trailing workdir switch, i.e.
  `USER <you>` and `WORKDIR /workspace`, to your `Containerfile`.

#### Addon configuration (`config.<addon>.yaml`)

An addon config file has the shape

```yaml
environment:
  - VAR1
  - VAR2=value
volumes:
  - /host/path:/path/in/container
  - ENV[DEV_DATA_DIR]/data:/data:ro
```

- `environment` entries are either ``NAME`` or ``NAME=value``:
  a bare ``NAME`` is taken from the host and passed into the
  container (variables which are not set on the host are skipped
  with a message), while ``NAME=value`` passes ``value`` into the
  container as-is, without consulting the host.
- `volumes` lists `host:container[:mode]` specs. The mode defaults to
  `rw` when omitted (`ro` mounts read-only). The spec is split on `:`
  into its host path, container path and (optional) mode parts, and
  each part is then expanded separately:

  1. `ENV[VAR]` is replaced by the value of the environment variable
     `VAR`. If `VAR` is not set, the whole spec is skipped with a
     message.
  2. `~` is expanded to the home directory and `${VAR}` to the value
     of `VAR`; unset variables are left unchanged.

  Messages about skipped entries are only shown at the highest
  verbosity (`-vvv`).

### Base images

This section explains the base image level, on which every project
image is built; the project level is described in
[Project configuration](#project-configuration).

#### A project `Containerfile` must be based on a dev-isolator base image

The single `FROM` instruction of a project's `Containerfile` must
reference a dev-isolator base image, i.e. an image named
`localhost/dev-isolator-...` or `dev-isolator-...`:

```dockerfile
FROM localhost/dev-isolator-ubuntu:26.04
```

The short form `FROM dev-isolator-ubuntu:26.04` is accepted as well
(`localhost/` is how podman names locally built images). A
`Containerfile` based on an unprepared image such as
`FROM ubuntu:26.04` makes every dev-isolator command abort with:

```
RuntimeError: The setup in .../.devisolator/Containerfile does not use
a dev-isolator image as base.
```

#### Where a base image comes from

Base images are built from the Containerfiles bundled with
dev-isolator (`data/base/Containerfile.<name>`, for example
`Containerfile.ubuntu-26.04`). Each file's first comment block names
the distribution it is derived from:

```dockerfile
# DI_BASE = ubuntu:26.04
FROM ubuntu:26.04
RUN apt-get update && apt install -y cmake build-essential
```

A base image built from such a file is tagged
`dev-isolator-<name>:<tag>`, where the name and tag are the parts of
the `DI_BASE` value around its `:` — so the file above produces
`dev-isolator-ubuntu:26.04` (shown as
`localhost/dev-isolator-ubuntu:26.04` in the local podman image store).
Project `Containerfile`s reference exactly these tags.

The bundled base images only use `apt` or `dnf` based distributions
(Ubuntu, Debian, Fedora, AlmaLinux, RockyLinux, …). This matters: a
base image carries the host user block, which requires `useradd` and
`getent` to exist (see
[Project configuration](#project-configuration)). Currently, Ubuntu
22.04, 24.04 and 26.04 base images ship with dev-isolator; a
`Containerfile` referencing any other image aborts with a
`FileNotFoundError` ("Could not find the base image for ...").

#### What goes into the base image, what goes into the project image

The base image keeps everything *shared* by all projects (and tag
variants) of a machine:

- the base setup of the bundled base Containerfile,
- the host user block: your host UID, GID and username are set up
  (removing any pre-existing user/group occupying those properties),
- the enabled addons rendered into container instructions
  (`addon.<name>.jinja2` files; see
  [Project configuration](#project-configuration) for how the addons
  are enabled).

Your project contributes its own steps in the project-level build: the
contents of the project `Containerfile` (system packages etc.) plus
the trailing workdir switch, `USER <you>` and `WORKDIR /workspace`.

Because addons and the host user block are baked into the base image,
changing `config.yaml` (or your addons) triggers a rebuild of the base
image on the next run; the project `Containerfile` does not need the
host user steps anymore.

#### Base image cleanup

`clean --all` only removes images whose repository is exactly
`dev-isolator` (the project images) — see [clean](#clean). The base
images are named `dev-isolator-<name>:<tag>` and are deliberately not
removed by `clean`: being shared by all projects, they stay
installed until you remove them manually with
`podman rmi dev-isolator-ubuntu:26.04`.

#### Bundle Base Images and Addons

- Base images:
  - `ubuntu:22.04`, `ubuntu:24.04`, `ubuntu:26.04` using Ubuntu as base 
  - `fedora:43 `, `fedora:44`, `fedora:45`, and `fedora:rawhide` using Fedora 
     as base. 
  - `almalinux:9` and `almalinux:10` using AlmaLinux (RHEL) as base. 
- Addons:
  - `opencode`, installs the `opencode` cli tool 
  - `claude`, installs the `claude` cli tool 

### Typical workflow

```bash
cd /home/user/projects/my-project
mkdir -p .devisolator
cat > .devisolator/Containerfile <<'EOF'
FROM localhost/dev-isolator-ubuntu:24.04
RUN apt-get update && apt-get install --yes build-essential python3-pip python3-venv
EOF
echo "addons: [opencode]" > .devisolator/config.yaml

dev-isolator build          # builds the shared base image (once), then
                            # dev-isolator:home-user-projects-my-project--default
dev-isolator                # open a bash inside the container
dev-isolator run make test  # or run a single command
dev-isolator clean          # remove the image again (the base image stays)
```

## Examples: a `.devisolator` folder

This section walks through a complete `.devisolator` folder for a small
Python project and shows what each file type looks like. Put the folder
in the root of your project:

```
my-project/
├── .devisolator/
│   ├── Containerfile        # based on a dev-isolator base image + system packages
│   ├── config.yaml          # which addons to enable + container settings
│   ├── addon.python.jinja2  # (optional) extra image steps for an addon
│   └── config.python.yaml   # (optional) runtime settings for that addon
└── README.md
```

### `Containerfile`

Your project's system-level setup. Its single `FROM` instruction must
be based on a dev-isolator base image (see
[Base images](#base-images)) — for example, the bundled `ubuntu` base
image. The base image is where the host user with your UID/GID comes
from (so `useradd` and `getent` must exist there); the project build
always ends with `USER <you>` and `WORKDIR /workspace`.

```dockerfile
FROM localhost/dev-isolator-ubuntu:24.04
RUN apt-get update && apt-get install --yes \
        build-essential python3 python3-pip python3-venv
```

### `config.yaml`

Selects the addons to append and, optionally, passes environment
variables and extra volume mounts to the container:

```yaml
addons:
  - python
  - opencode
environment:
  - OMP_NUM_THREADS
  - PYTHONDONTWRITEBYTECODE
  - PIP_INDEX_URL=https://example.com/simple
volumes:
  - /tmp:/host_tmp
```

- `addons` lists the addon names to append (see
  `addon.<name>.jinja2` below and the bundled `opencode`/`claude`
  addons). Without this file, every `addon.*.jinja2` is enabled.
- `environment` entries are either `NAME` (a host variable to forward
  into the container; unset ones are skipped) or `NAME=value`
  (a value passed into the container as-is, without consulting
  the host).
- `volumes` lists `host:container[:mode]` mounts; `mode` defaults to
  `rw`. The project directory is always bind-mounted at `/workspace`
  and takes precedence over anything declared here.

### `addon.<name>.jinja2`

A jinja2 template with additional Containerfile instructions for the
addon `<name>`. It is rendered and appended to the *base image's*
Containerfile at base image build time (see
[Base images](#base-images)). These variables are available:

- `uid`, `gid` — your host UID and GID
- `username` — a user name usable for those ids
- `home_dir` — your host home directory

For example, `addon.python.jinja2` installs a tool as root and then
drops back to your user:

```dockerfile
USER root
RUN apt-get install --yes --no-install-recommends poetry && \
    rm -rf /var/lib/apt/lists/*
USER {{ username }}
ENV PATH="{{ home_dir }}/.local/bin:${PATH}"
```

Every `addon.*.jinja2` in `.devisolator/` is collected after the
bundled ones (sorted by file name); duplicates are removed.

### `config.<addon>.yaml`

Runtime settings an addon (or the project itself, via `config.yaml`)
requests for the container. It has the same `environment`/`volumes`
shape:

```yaml
environment:
  - PIP_CACHE_DIR
  - PIP_DEFAULT_TIMEOUT=60
volumes:
  - ${HOME}/.cache/python:${HOME}/.cache/python:ro
  - ENV[PIP_CACHE_DIR]:ENV[PIP_CACHE_DIR]
```

Each part of a volume spec (after the spec has been split on `:` into
host path, container path and optional mode) is expanded as follows:

1. `ENV[VAR]` is replaced by the value of the environment variable
   `VAR`; if the variable is unset the whole spec is skipped.
2. `~` and `${VAR}` are then expanded; unset variables are left
   unchanged.

### What gets built

For the folder above, dev-isolator builds two images:

1. the shared base image `dev-isolator-ubuntu:24.04` — the bundled
   base Containerfile, followed by the host-user block (`USER <you>`)
   and the enabled addon templates (`addon.python.jinja2`, then the
   bundled `addon.opencode.jinja2`), built once and reused;
2. the project image `dev-isolator:<slug_of_my-project>--default`
   (for the path above `dev-isolator:home-user-projects-my-project--default`)
   — your `Containerfile` followed by
   `USER <you>` and `WORKDIR /workspace`.

Running `dev-isolator` then mounts `my-project/` at `/workspace`
inside a container running as your own UID/GID, so files you create
stay owned by you.

## Contributing

Contributions are welcome from everyone; please follow the guide in
[CONTRIBUTING.md](CONTRIBUTING.md):

## Licensing

Copyright (C) 2026 Martin Köhler

`dev-isolator` is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version (GPL-3.0-or-later).

See [LICENSE](LICENSE) for details.

