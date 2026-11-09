# dev-isolator

`dev-isolator` is a command line tool that manages a per-project,
[podman](https://podman.io/) based development environment.

**Requirements:**

- Linux with a working podman installation.
- Python 3.10 or later.
- Python dependencies:
  - [`podman`](https://podman-py.readthedocs.io/en/latest/)
  - [`pyyaml`](https://pypi.org/project/PyYAML/)
  - [`jinja2`](https://jinja.palletsprojects.com/en/stable/)
  - [`tree-sitter`](https://github.com/tree-sitter/tree-sitter) and
    [`tree-sitter-containerfile`](https://github.com/wharflab/tree-sitter-containerfile/)
  - [`pytest`](https://docs.pytest.org/en/stable/), only used by the test-suite
    (but currently installed with dev-isolator as a regular dependency)

Both `dev-isolator` and `di` (a short alias) start the tool.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Command line overview](#command-line-overview)
  - [`init`](#init)
  - [`build`](#build)
  - [`shell`](#shell)
  - [`run`, `opencode`, `claude`](#run-opencode-claude)
  - [`clean`](#clean)
  - [`list-base`](#list-base)
  - [Discovery of the project directory](#discovery-of-the-project-directory)
- [Sub-configurations (`--config`)](#sub-configurations---config)
  - [What a sub-configuration overrides](#what-a-sub-configuration-overrides)
  - [Naming: every sub-configuration gets its own image](#naming-every-sub-configuration-gets-its-own-image)
  - [Example: a project with a `gpu` sub-configuration](#example-a-project-with-a-gpu-sub-configuration)
- [Project configuration](#project-configuration)
  - [Addon configuration (`config.<addon>.yaml`)](#addon-configuration-configaddonyaml)
  - [Run arguments (`run_arguments`)](#run-arguments-run_arguments)
- [Base images](#base-images)
  - [What a project `Containerfile`'s `FROM` references](#what-a-project-containerfiles-from-references)
  - [Where a base image comes from](#where-a-base-image-comes-from)
  - [What goes into the base image, what goes into the project image](#what-goes-into-the-base-image-what-goes-into-the-project-image)
  - [Base image cleanup](#base-image-cleanup)
  - [Bundle base images and addons](#bundle-base-images-and-addons)
- [Typical workflow](#typical-workflow)
- [Examples: a `.devisolator` folder](#examples-a-devisolator-folder)
  - [What gets built](#what-gets-built)
- [Contributing](#contributing)
- [Licensing](#licensing)

## Installation

Before using `dev-isolator`, [podman](https://podman.io/) must be enabled as a
user service, typically with:

```shell
systemctl --user enable --now podman.service
```

Then install `dev-isolator` from a source checkout:

```bash
pip install -e .
```

Alternatively, install it with
[`pipx`](https://pipx.pypa.io/stable/), which runs the CLI in an
isolated virtual environment without touching your main Python
environment:

```bash
pipx install . 
```
from the source directory.

Both `dev-isolator` and `di` end up on your `PATH` this way.

Run the test suite with `pytest`.

## Usage

### Command line overview

```
dev-isolator [--verbose/-v]... [--shell/-s <shell>] [--config/-c <name>] <subcommand> [arguments]
```

**Global options:**

| Option | Description |
|---|---|
| `--verbose`, `-v` | Increase verbosity; repeat for more detail: `-v` (info), `-vv` (debug), `-vvv` (trace, e.g. the merged Containerfile and build logs). |
| `--shell`, `-s` | Shell to start inside the container (default `bash`). Applies to `shell` and to an interactive session started by `run`/`opencode`/`claude` with no command. |
| `--config`, `-c` | Name of a sub-configuration: a subdirectory of `.devisolator/` with its own `Containerfile` and `config.yaml` (see [Sub-configurations (`--config`)](#sub-configurations---config)). An explicit `--config` wins over the `DEVISOLATOR_CONFIG` environment variable. |

**Subcommands:**

| Subcommand | Description |
|---|---|
| `init` | Initialize the local `.devisolator` configuration. |
| `build` | Build the image of the current project. |
| `shell` | Open an interactive shell inside the container. |
| `run <command> [args...]` | Run an arbitrary command inside the container. |
| `opencode [args...]` | Run [opencode](https://opencode.ai/) inside the container. |
| `claude [args...]` | Run [claude](https://claude.ai/) inside the container. |
| `clean` | Remove dev-isolator images. |
| `list-base` | List the base image definitions bundled with dev-isolator. |

Without an explicit subcommand, `shell` runs with the default shell (`bash`).
`dev-isolator help` (or `-h` on any subcommand) prints the help. `opencode`,
`claude` and `run` pass all following (unknown) arguments through to
the command inside the container; their own options are not parsed by
dev-isolator.

### `init`

```bash
dev-isolator init
dev-isolator init -b fedora:43    # base the new Containerfile on Fedora 43
dev-isolator --config gpu init    # initialize the "gpu" sub-configuration
```

Initializes the local configuration of the current project (see
[Typical workflow](#typical-workflow)): it creates the `.devisolator` directory
and fills it with the `Containerfile` and `config.yaml` bundled with
dev-isolator:

- `config.yaml` enables the bundled `opencode` and `claude` addons and
  declares no extra `environment`/`volumes` settings; edit it to your needs
  (see [Project configuration](#project-configuration)).
- The `Containerfile`'s single `FROM` instruction references a base image from
  which the dev-isolator base image is built. By default that is `ubuntu:26.04`;
  with `--base`/`-b` a different bundled base image can be selected — pass a
  plain base name such as `ubuntu:24.04`, exactly as printed by `list-base`
  (see [Base images](#base-images)).

Existing files are kept and reported; pass `--force`/`-f` to overwrite them.
Like `list-base`, `init` needs no pre-existing `.devisolator` directory, so it
can be run in a fresh checkout; an unknown base image aborts with an error
(exit code 255).

With `--config <name>`, `init` creates the sub-configuration directory
`.devisolator/<name>/` instead (see
[Sub-configurations (`--config`)](#sub-configurations---config)) and writes the
same bundled files there — the sub-configuration then overrides the project's
default `Containerfile` and `config.yaml` per the lookup precedence described
below (`--base` and `--force` apply in the same way). Note that `--config
default` explicitly selects the default configuration, i.e. it is ignored here
as everywhere else, and the bare `.devisolator/` directory is initialized.
### `build`

```bash
dev-isolator build
```

Builds the image of the current project:

- The base image referenced by the project's `Containerfile` is built first, on
  every invocation (the build cache keeps it cheap when nothing changed). It is
  built from a Containerfile bundled with dev-isolator, merging the base setup,
  the appended host user block and the enabled addon templates, and tagged
  `dev-isolator-<name>:<tag>-<hash>` (see [Base images](#base-images)). Being
  shared, it is built once per base setup: a project `Containerfile` change does
  not rebuild it, but addon or `config.yaml` changes do.
- The project's `Containerfile` (from `<project>/.devisolator/`) must be based
  on that base image and is built from the build context (the directory
  containing `.devisolator`). The build always ends with `USER <you>` and
  `WORKDIR /workspace`.
- If a previous image carried the same tag but the build produced a different
  image id, the superseded image is removed so no dangling image remains.

Building aborts when the base image referenced by the `Containerfile` cannot be
run or none of the supported package managers is available in it (see
[Base images](#base-images)).

**Options:**

| Option | Description |
|---|---|
| `--keep-container-file`, `-k` | On a build error, keep the generated (merged) Containerfile in the current directory as `Containerfile-fail-*` for inspection. |

### `shell`

```bash
dev-isolator shell
dev-isolator              # equivalent, shell is the default
dev-isolator --shell zsh  # start zsh instead of bash
```

Opens an interactive shell inside the container:

1. The image is built first (as with `build`); the build cache keeps
   rebuilding cheap.
2. A container named
   `dev-isolator-<slug_of_the_project_path>--<tag>-<8 random characters>` is
   created and started. The random suffix lets several containers of the same
   project run in parallel. It runs `sleep infinity` as its entrypoint, so it
   stays alive for the session, and with `--userns=keep-id` it uses the host
   user's UID/GID, so files created in the `/workspace` bind mount stay owned by
   you.
3. The current working directory is mounted read-write at `/workspace`, plus the
   environment variables, volumes and `run_arguments` declared in `config.yaml`
   and requested by the enabled addons (see
   [Project configuration](#project-configuration) and
   [Run arguments (`run_arguments`)](#run-arguments-run_arguments)).
4. Your terminal attaches to a `podman exec -it` session running the shell
   (`bash` by default); leave the session with `exit` or Ctrl-D.
5. After the session ends, the container is stopped and removed.

The global `--shell`/`-s` option (see [Command line overview](#command-line-overview))
selects the shell started here.

### `run`, `opencode`, `claude`

```bash
dev-isolator run make test
dev-isolator opencode --port 4096
dev-isolator claude --model xyz
```

Work exactly like `shell`, except that instead of an interactive shell the given
command is executed inside the container (via `podman exec -it`) and all
following arguments are passed to it unchanged. The command must exist inside the
image; `opencode` and `claude` are provided by the respective bundled addons.
`run` with no command falls back to a shell. The session exit code is returned
and the container is stopped and removed afterwards.

### `clean`

```bash
dev-isolator clean
dev-isolator clean --all
dev-isolator clean --local
```

Removes the image of the current project (`clean`), every image whose repository
is `dev-isolator` (`clean --all`), or every dev-isolator image of the current
project including all its tag variants (`clean --local`).

`clean` removes the image of the currently selected configuration: the project
default (`dev-isolator:<slug>--default`) or — when
[`--config`](#sub-configurations---config) is in effect — the selected
sub-configuration's image. `clean --all` and `clean --local` remove images
regardless of the selection.

Before removal, all matching containers are stopped (killed after 10 seconds) and
removed. A container matches when it was created from one of the images or its
name starts with the project's container name prefix (`dev-isolator-<slug>--<tag>`
for `clean`, `dev-isolator-<slug>` for `clean --local`, `dev-isolator-` for
`clean --all`). With `--verbose` the removed containers and images are printed.

Base images (`dev-isolator-<name>:<tag>-<hash>`, e.g.
`dev-isolator-ubuntu:24.04-<hash>`) are never removed by `clean`; see
[Base image cleanup](#base-image-cleanup).

**Options:**

| Option | Description |
|---|---|
| `--all`, `-a` | Remove all dev-isolator images instead of only the current one. |
| `--local`, `-l` | Remove every dev-isolator image of the current project (all tag variants) instead of only the current one. Mutually exclusive with `--all`. |

### `list-base`

```bash
dev-isolator list-base
```

Lists the base image definitions bundled with dev-isolator, one per line as a
plain base name, for example `ubuntu:24.04`. These are the names a project
[`Containerfile`](#base-images) can use in its `FROM` instruction (and the
`--base`/`-b` option of [`init`](#init) accepts), resolved to the matching
bundled base Containerfile (see [Base images](#base-images)). Unlike the other
subcommands, `list-base` needs no initialized `.devisolator` directory and
works from any location.

### Discovery of the project directory

The project directory holds the `.devisolator` folder and is found by searching
for a `.devisolator` directory from the current working directory and walking up
the directory tree (to the home directory, or the filesystem root for working
directories outside the home tree); the first hit wins. If none is found, the
`Containerfile` and configuration bundled with dev-isolator are used.

#### `--config`: selecting a sub-configuration

Sub-configurations are selected from this project directory with
`--config <name>` or the `DEVISOLATOR_CONFIG` environment variable; see
[Sub-configurations (`--config`)](#sub-configurations---config).

### Sub-configurations (`--config`)

Large projects often need more than one container environment — a slim default
for everyday work and a specialized one for GPU builds or documentation. Each
variant lives in its own subdirectory of `.devisolator/` and is selected per
invocation (or per session) with `--config`:

```bash
dev-isolator --config gpu build    # build the "gpu" sub-configuration
dev-isolator --config gpu shell    # open a shell in it
```

The same selection can be made through the environment variable
`DEVISOLATOR_CONFIG`; an explicit `--config` wins:

```bash
export DEVISOLATOR_CONFIG=gpu      # "gpu" for every call in this shell
dev-isolator build                 # gpu, no --config needed
dev-isolator --config base build   # an explicit --config overrides the variable
```

#### What a sub-configuration overrides

For a sub-configuration `<name>`, the `Containerfile` and the `config.yaml` are
looked up in this order, and the *first found file wins* (the files are never
merged):

1. `.devisolator/<name>/` — the sub-configuration itself.
2. `.devisolator/` — the project's default configuration.
3. The files bundled with dev-isolator.

A sub-configuration is created with `dev-isolator --config <name> init`, which
writes the bundled `Containerfile` (rewritten to the requested `--base` image)
and `config.yaml` directly into `.devisolator/<name>/` (see
[`init`](#init)) — everything the directory does not contain then falls back
through this chain.

Because the project defaults sit in the middle of this chain, a sub-configuration
only *overrides* the files it actually contains: a `gpu/` directory holding just a
`Containerfile` reuses the default `config.yaml`, and a `config.yaml` alone
reuses the default `Containerfile`. If the named directory does not exist at all,
dev-isolator aborts with an error (exit code 255) — sub-configurations are
strictly project-local; the bundled defaults know no subdirectories.

The name `default` is reserved: `--config default` (or
`DEVISOLATOR_CONFIG=default`) explicitly selects the default configuration and is
equivalent to omitting the option entirely, including the resulting `--default`
image tag. A directory `.devisolator/default/`, should one exist, is never used
as a sub-configuration.

The enabled addon templates (`addon.*.jinja2`) are *collected* from all three
places (each directory sorted by file name, duplicates removed), so they can be
added per sub-configuration, per project or come from the bundle. Which of them
are enabled — and which `environment`/`volumes` settings are applied — is decided
by the single active `config.yaml` (first found), exactly as for the default
configuration.

One file type is deliberately *not* part of a sub-configuration: the per-addon
runtime settings (`config.<addon>.yaml`) are always read from the project's
`.devisolator/` directory (first found) or, when missing there, from the bundled
ones. This keeps the runtime environment/volume wiring shared across all
sub-configurations.

#### Naming: every sub-configuration gets its own image

The sub-configuration name doubles as the image tag. Building under `--config gpu`
produces the image `dev-isolator:<slug_of_the_project>--gpu` (with the default
configuration the tag is `default`), and the container is created as
`dev-isolator-<slug_of_the_project>--gpu`. Several sub-configurations of a project
can therefore be built and kept side by side, and `build`, `shell`, `run`,
`opencode` and `claude` always act on the currently selected one.
[`clean`](#clean) removes the selected configuration's image; use
`clean --local` to drop every tag variant of the project at once.

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

The configuration of a containerized dev environment lives in the `.devisolator`
directory of your project:

- `Containerfile`: the base image definition (see [Base images](#base-images)).
- `config.yaml`: the enabled addons and container settings. Besides `addons`, it
  can also declare `environment` variables, additional `volumes`, build options
  and `run_arguments` for the container (see
  [Run arguments (`run_arguments`)](#run-arguments-run_arguments)):

  ```yaml
  addons:
    - opencode
    - claude
  environment:
    - OMP_NUM_THREADS
    - PYTHONDONTWRITEBYTECODE=1
  volumes:
    - /tmp:/host_tmp
  build:
    # Use the project directory as the build context (needed for
    # ADD/COPY instructions); by default an empty temporary directory
    # holding only the Containerfile is used.
    local_context: false
    # Ignore the build cache; by default the cache is used.
    nocache: false
  ```

  The `build` section controls the image build: `local_context` selects the
  build context — by default an empty temporary directory holding only the
  merged Containerfile (the project sources stay on the host and are mounted
  at `/workspace` at run time), but when set the project's own directory is
  used instead so `ADD`/`COPY` instructions can read the sources; `nocache`
  ignores the build cache. Both default to `false`.

- `addon.<name>.jinja2`: a jinja2 template with the Containerfile instructions
  appended for the addon `<name>`. It is rendered into the *base image's*
  Containerfile (see [Base images](#base-images)) and receives the variables
  `uid`, `gid`, `username` and `home_dir` of the invoking host user.
- `config.<addon>.yaml`: runtime settings an addon requests for its container (see
  [Addon configuration](#addon-configuration-configaddon-yaml)).

Configuration resolution rules:

- For `config.yaml` and `config.<addon>.yaml`, strictly the first file found is
  used: the file in `<project>/.devisolator/` if it exists, otherwise the one
  bundled with dev-isolator. Only one is active; the two are never merged.
- The `addons` list names the addons to append to the Containerfile. Without any
  `config.yaml` at all, every `addon.*.jinja2` file is enabled.
- The `environment` and `volumes` sections of `config.yaml` are processed exactly
  like those of an addon config file (see
  [below](#addon-configuration-configaddon-yaml)): `environment` entries are either
  `NAME` (taken from the host) or `NAME=value` (used as given), and `volumes`
  lists `host:container[:mode]` specs. Addon settings override those from
  `config.yaml`; the mount of the current directory at `/workspace` always takes
  precedence.
- Addon templates are collected first from the directory bundled with dev-isolator,
  then from `.devisolator/` (sorted by file name); duplicates are removed. They are
  rendered into the base image's Containerfile, not into the project one.
- The host user block is likewise appended to the base image's Containerfile: a
  user with the same UID, GID and username as the invoking host user is created
  (removing any pre-existing user/group occupying those properties) and made the
  image's default user. The project image build appends only the trailing workdir
  switch, `USER <you>` and `WORKDIR /workspace`, to your `Containerfile`.

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

- `environment` entries are either `NAME` or `NAME=value`: a bare `NAME` is taken
  from the host and passed into the container (variables which are not set on the
  host are skipped with a message), while `NAME=value` passes the value in
  as-is, without consulting the host.
- `volumes` lists `host:container[:mode]` specs. The mode defaults to `rw` when
  omitted (`ro` mounts read-only). The spec is split on `:` into its host path,
  container path and (optional) mode parts, and each part is then expanded
  separately:

  1. `ENV[VAR]` is replaced by the value of the environment variable `VAR`. If
     `VAR` is not set, the whole spec is skipped with a message.
  2. `~` is expanded to the home directory and `${VAR}` to the value of `VAR`;
     unset variables are left unchanged.

Messages about skipped entries are only shown at the highest verbosity (`-vvv`).

#### Run arguments (`run_arguments`)

The optional `run_arguments` key of `config.yaml` is a mapping of keyword
arguments of podman's `containers.create()` to their values; they are passed
to the container when it is created:

```yaml
run_arguments:
  # Allocate a pseudo-TTY and keep STDIN open
  tty: true
  stdin_open: true
  # Optional hostname and working directory inside the container
  hostname: my-dev-container
  working_dir: /workspace
  # Resource limits: memory (bytes or "128m"/"1g") and CPU quota
  # in units of 1e-9 CPUs (2000000000 = 2 CPUs)
  mem_limit: 1g
  nano_cpus: 2000000000
  # Restrict execution to specific CPUs ("0-3" or "0,1")
  cpuset_cpus: 0-3
  # Expose host devices: <host>:<container>:<permissions>
  devices:
    - /dev/sda:/dev/xvda:rwm
  # Add or drop kernel capabilities
  cap_add:
    - SYS_ADMIN
    - MKNOD
  # Network mode: "bridge", "none", "host" or "container:<name|id>"
  network_mode: bridge
  # Port bindings: keys are the container ports, values the host ports
  ports:
    2222/tcp: 3333
  # Kernel parameters, DNS servers, extra hostnames, labels, ...
  sysctls:
    net.ipv4.ip_forward: "1"
  dns:
    - 8.8.8.8
  extra_hosts:
    myhost: 192.168.1.10
  labels:
    my-label: my-value
  # Size of /dev/shm
  shm_size: 1G
  # Restart behaviour on exit
  restart_policy:
    Name: on-failure
    MaximumRetryCount: 5
```

Only the most common `containers.create()` arguments are supported; the full
list is in `SUPPORTED_RUN_ARGUMENTS` in `src/dev_isolator/config.py`. An
unsupported key, or one of the arguments dev-isolator manages itself (`image`,
`command`, `name`, `entrypoint`, `environment`, `volumes`, `detach`), raises
an error when the container is created. See the commented example in the
bundled [`config.yaml`](src/dev_isolator/data/config.yaml) for all supported
entries.

Defaults: `userns_mode` defaults to `keep-id` — the container runs with the
same UID/GID as the host user, so files created in the `/workspace` bind
mount stay owned by the user — and `network_mode` defaults to `host`. Both
can be overridden via `run_arguments`. An empty or missing `run_arguments`
key means the defaults are used.

### Base images

This section explains the base image level, on which every project image is
built; the project level is described in
[Project configuration](#project-configuration).

#### What a project `Containerfile`'s `FROM` references

The single `FROM` instruction of a project's `Containerfile` must reference a
base image dev-isolator can build the dev-isolator base image from. The
simplest form is a plain base name matching one of the bundled base images
(see [Bundle base images and addons](#bundle-base-images-and-addons)):

```dockerfile
FROM ubuntu:26.04
```

This is what the `Containerfile` written by `init` uses by default. dev-isolator
resolves the name to the matching bundled base Containerfile (see
[below](#where-a-base-image-comes-from)) and builds the dev-isolator base image
from it.

Any other image works as well, as long as one of the supported package managers
(`apt`, `dnf`, `zypper` or `pacman`) is available in it: dev-isolator runs the
image in a short-lived container to detect the package manager, and builds the
base image from the bundled Containerfile of that package manager. The `FROM`
image must carry a tag; a podman error is raised when the image cannot be run,
and dev-isolator aborts with `RuntimeError: Package manager ... not supported.`
when none of the supported package managers is found.

#### Where a base image comes from

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
[above](#what-a-project-containerfiles-from-references)) selects the bundled
base Containerfile.

The built base image is tagged `dev-isolator-<name>:<tag>-<hash>`, where
`name` and `tag` are the parts of the project `Containerfile`'s `FROM` image
around its `:` — so a `FROM ubuntu:26.04` produces
`dev-isolator-ubuntu:26.04-<hash>` (podman shows the image with a
`localhost/` registry prefix in the local image store, e.g.
`localhost/dev-isolator-ubuntu:26.04-<hash>`).
The hash is the SHA-256 hash of the merged base Containerfile content (see
[below](#what-goes-into-the-base-image-what-goes-into-the-project-image)), so
rebuilds with a different setup get their own tag. The project image build
rewrites the `FROM` instruction of your `Containerfile` to this hashed tag,
so it always references the base image matching the current setup.

#### What goes into the base image, what goes into the project image

The base image keeps everything *shared* by all projects (and tag variants) of a
machine:

- the base setup of the bundled base Containerfile,
- the host user block: your host UID, GID and username are set up (removing any
  pre-existing user/group occupying those properties),
- the enabled addons rendered into container instructions (`addon.<name>.jinja2`
  files; see [Project configuration](#project-configuration) for how the addons
  are enabled).

Your project contributes its own steps in the project-level build: the contents of
the project `Containerfile` (system packages etc.) plus the trailing workdir
switch, `USER <you>` and `WORKDIR /workspace`.

Because addons and the host user block are baked into the base image, changing
`config.yaml` (or your addons) triggers a rebuild of the base image on the next
run; the project `Containerfile` does not need the host user steps anymore.

#### Base image cleanup

`clean --all` only removes images whose repository is exactly `dev-isolator` (the
project images) — see [clean](#clean). The base images are named
`dev-isolator-<name>:<tag>-<hash>` and are deliberately not removed by `clean`:
being shared by all projects, they stay installed until you remove them manually
with `podman rmi dev-isolator-ubuntu:26.04-<hash>` (the hash can be found with
`podman images`, or with `--verbose`/`-vvv` during a build).

#### Bundle base images and addons

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

### Typical workflow

```bash
cd /home/user/projects/my-project

# Create .devisolator/ with a Containerfile and config.yaml
# (base the Containerfile on ubuntu:24.04):
dev-isolator init -b ubuntu:24.04

cat >> .devisolator/Containerfile <<'EOF'
RUN apt-get update && apt-get install --yes build-essential python3-pip python3-venv
EOF
echo "addons: [opencode]" >> .devisolator/config.yaml

dev-isolator build          # builds the shared base image (once), then
                            # dev-isolator:home-user-projects-my-project--default
dev-isolator                # open a bash inside the container
dev-isolator run make test  # or run a single command
dev-isolator clean          # remove the image again (the base image stays)
```

The same setup can also be created by hand: create the `.devisolator` folder in
the project root and write the files yourself:

```bash
cd /home/user/projects/my-project
mkdir -p .devisolator
cat > .devisolator/Containerfile <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install --yes build-essential python3-pip python3-venv
EOF
echo "addons: [opencode]" > .devisolator/config.yaml
```

## Examples: a `.devisolator` folder

This section walks through a complete `.devisolator` folder for a small Python
project. Put the folder in the root of your project; the file types and how
`environment`/`volumes` are processed are covered in
[Project configuration](#project-configuration) and
[Addon configuration](#addon-configuration-configaddon-yaml).

```
my-project/
├── .devisolator/
│   ├── Containerfile        # based on a dev-isolator base image + system packages
│   ├── config.yaml          # which addons to enable + container settings
│   ├── addon.python.jinja2  # (optional) extra image steps for an addon
│   └── config.python.yaml   # (optional) runtime settings for that addon
└── README.md
```

The `Containerfile`'s single `FROM` references the base image from which the
dev-isolator base image is built (see
[Base images](#base-images)); that base image provides the host user with your
UID/GID (so `useradd` and `getent` must exist there); the project build always
ends with `USER <you>` and `WORKDIR /workspace`.

### What gets built

For the folder above, dev-isolator builds two images:

1. the shared base image `dev-isolator-ubuntu:24.04` (the actual tag carries a
   content hash, see [Base images](#base-images)) — the bundled base Containerfile,
   the host-user block (`USER <you>`) and the enabled addon templates (`addon.python.jinja2`,
   then the bundled `addon.opencode.jinja2`), built once and reused;
2. the project image `dev-isolator:<slug_of_my-project>--default` (for the path
   above `dev-isolator:home-user-projects-my-project--default`) — your
   `Containerfile` followed by `USER <you>` and `WORKDIR /workspace`.

Running `dev-isolator` then mounts `my-project/` at `/workspace` inside a container
running as your own UID/GID, so files you create stay owned by you.

## Contributing

Contributions are welcome from everyone; please follow the guide in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licensing

Copyright (C) 2026 Martin Köhler

`dev-isolator` is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version (GPL-3.0-or-later).

See [LICENSE](LICENSE) for details.
