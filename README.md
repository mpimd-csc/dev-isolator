# dev-isolator

`dev-isolator` is a command line tool that manages a per-project,
[podman](https://podman.io/) based development environment.

For every project containing a `.devisolator` directory (holding a
`Containerfile` and optional addon configuration) it builds a container
image named `dev-isolator-<slug_of_the_project_path>` and runs commands
inside a container created from that image. The current working
directory is mounted into the container at `/workspace`. This keeps
build tools, caches and dependencies isolated from the host, while the
sources stay in place.

Requirements:

- Linux with a working podman installation (the podman socket/service
  must be reachable through the `podman` Python bindings).
- Python 3.10 or later.
- Python packages: `podman`, `pyyaml`, `jinja2` (installed
  automatically; `pytest` is installed too and used by the test suite).

Both commands `dev-isolator` and `di` (a short alias) start the tool.

## Installation

Install from PyPI:

```bash
pip install dev-isolator
```

Or install from a source checkout for development:

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
dev-isolator [--verbose/-v]... <subcommand> [arguments]
```

Global option:

| Option | Description |
|---|---|
| `--verbose`, `-v` | Increase verbosity. Repeat for more details: `-v` (info), `-vv` (debug), `-vvv` (trace, e.g. prints the merged Containerfile and build logs). |

Subcommands:

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

- The project's `Containerfile` (from `<project>/.devisolator/`)
  is merged with the host user block and every enabled addon template
  (see [Project configuration](#project-configuration)) and built from
  the build context (the directory containing the `.devisolator`
  directory).
- The base image given in the `Containerfile` is pulled fresh, the
  build is tagged `dev-isolator:<slug_of_the_project_path>` and
  intermediate containers are removed afterwards.
- If a previous image carried the same tag but the build produced a
  different image id, the superseded image is removed so no dangling
  image is left behind.

Options:

| Option | Description |
|---|---|
| `--nocache`, `-n` | Pass `--nocache` to `podman build`, i.e. ignore the build cache. |

### `shell`

```bash
dev-isolator shell
dev-isolator              # equivalent, shell is the default
dev-isolator shell -s zsh # start zsh instead of bash
```

Opens an interactive shell inside the container:

1. The image is always built first (as with `build`, without
   `--nocache`), rebuilding is cheap thanks to the build cache.
2. A container named `dev-isolator-<slug_of_the_project_path>` is
   created (or reused if one created earlier exists) and started. It
   runs `sleep infinity` as its entrypoint, so it stays alive for the
   session. The container runs with `--userns=keep-id`, i.e. the same
   UID/GID as the host user, so files created in the `/workspace` bind
   mount stay owned by you.
3. The current working directory is mounted read-write at
   `/workspace`, plus the environment variables and volumes requested
   by the enabled addons (see
   [Addon configuration](#addon-configuration-configaddon-yaml)).
4. Your terminal is attached to a `podman exec -it` session running
   the shell (`bash` by default). Leave the session with `exit` or
   Ctrl-D.
5. After the session ends, the container is stopped and removed.

Options:

| Option | Description |
|---|---|
| `--shell <shell>`, `-s <shell>` | Shell to start inside the container. Default: `bash`. |

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
```

Removes the image of the current project (`clean`) or every image
tagged `dev-isolator:*` (`clean --all`).

Before removal, all containers based on the image(s) are stopped
(after 10 seconds they are killed) and removed. With `--verbose` the
removed containers and images are printed.

Options:

| Option | Description |
|---|---|
| `--all`, `-a` | Remove all dev-isolator images instead of only the current one. |

### Discovery of the project directory

The project directory is located by searching for a `.devisolator`
directory starting at the current working directory and walking up the
directory tree (up to the home directory, or the filesystem root for
working directories outside the home tree). The first hit wins.

If no `.devisolator` directory is found, the `Containerfile` and
configuration bundled with dev-isolator are used.

Hotkeys: none. Detach from an interactive session with Ctrl-D or
`exit` (the detach keys are empty).

### Project configuration

The configuration of a containerized dev environment lives in the
`.devisolator` directory of your project:

- `Containerfile`: The base image definition.
- `config.yaml`: Names the enabled addons:

  ```yaml
  addons_enabled:
    - opencode
    - claude
  ```

- `addon.<name>.jinja2`: A jinja2 template with the Containerfile
  instructions appended for the addon `<name>`. It receives the
  variables `uid`, `gid`, `username` and `home_dir` of the invoking
  host user.
- `config.<addon>.yaml`: Runtime settings an addon requests for its
  container (see below).

Configuration resolution rules:

- For `config.yaml` and `config.<addon>.yaml`, strictly the first file
  found is used: the file in `<project>/.devisolator/` if it exists,
  otherwise the one bundled with dev-isolator. Only one is active; the
  two are never merged.
- The `addons_enabled` list names the addons to append to the
  Containerfile. Without any `config.yaml` at all, every
  `addon.*.jinja2` file is enabled.
- Addon templates are collected first from the directory bundled with
  dev-isolator, then from `.devisolator/` (sorted by file name);
  duplicates are removed.
- When the image is built, a host user block is appended: a user with
  the same UID, GID and username as the invoking host user is created
  (removing any pre-existing user/group occupying those properties)
  and made the image's default user. The base image must be
  Debian-style (`useradd` and `getent` must exist). The build ends
  with `WORKDIR /workspace`.

#### Addon configuration (`config.<addon>.yaml`)

An addon config file has the shape

```yaml
environment:
  - VAR1
volumes:
  - /host/path:/path/in/container
  - ENV[DEV_DATA_DIR]/data:/data:ro
```

- `environment` lists the names of environment variables taken from
  the host and passed into the container. Variables which are not set
  on the host are skipped with a message.
- `volumes` lists `host:container[:mode]` specs. The mode defaults to
  `rw` when omitted (`ro` mounts read-only). Each spec may contain
  `ENV[VAR]`, `~` and `${VAR}` references. They are expanded in the
  following order, before the spec is parsed:

  1. `ENV[VAR]` is replaced by the value of the environment variable
     `VAR`. This happens first, so the value may itself contain the
     `:` separators of the spec. If the variable is not set, the
     whole spec is skipped with a message.
  2. `~` is expanded to the home directory and `${VAR}` to the value
     of `VAR`. Unset variables expand to an empty string.

### Bundled default configuration

When no `.devisolator` directory is found, dev-isolator uses its
bundled defaults:

- Base image: `FROM ubuntu:26.04`, installing `cmake` and
  `build-essential`.
- Addons `opencode` and `claude`, which install the respective AI
  coding tool into the image and put it on the `PATH`.

### Typical workflow

```bash
cd /home/user/projects/my-project
mkdir -p .devisolator
cat > .devisolator/Containerfile <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install --yes build-essential python3-pip python3-venv
EOF
echo "addons_enabled: [opencode]" > .devisolator/config.yaml

dev-isolator build          # build dev-isolator:home-user-projects-my-project
dev-isolator                # open a bash inside the container
dev-isolator run make test  # or run a single command
dev-isolator clean          # remove the image again
```

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

