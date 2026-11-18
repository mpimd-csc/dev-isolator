## Usage

## Contents

- [Command line overview](#command-line-overview)
- [`init`](#init)
- [`build`](#build)
- [`shell`](#shell)
- [`run`, `opencode`, `claude`](#run-opencode-claude)
- [`target`](#target)
- [`clean`](#clean)
- [`list-base`](#list-base)
- [`list-targets`](#list-targets)
- [Discovery of the project directory](#discovery-of-the-project-directory)
  - [`--config`: selecting a sub-configuration](#--config-selecting-a-sub-configuration)

### Command line overview

```
dev-isolator [--verbose/-v]... [--shell/-s <shell>] [--config/-c <name>] <subcommand> [arguments]
```

**Global options:**

| Option | Description |
|---|---|
| `--verbose`, `-v` | Increase verbosity; repeat for more detail: `-v` (info), `-vv` (debug), `-vvv` (trace, e.g. the merged Containerfile and build logs). |
| `--shell`, `-s` | Shell to start inside the container (default `bash`). Applies to `shell` and to an interactive session started by `run`/`opencode`/`claude` with no command. |
| `--config`, `-c` | Name of a sub-configuration: a subdirectory of `.devisolator/` with its own `Containerfile` and `config.yaml` (see [Sub-configurations (`--config`)](sub-configurations.md#sub-configurations---config)). An explicit `--config` wins over the `DEVISOLATOR_CONFIG` environment variable. |

**Subcommands:**

| Subcommand | Description |
|---|---|
| `init` | Initialize the local `.devisolator` configuration. |
| `build` | Build the image of the current project. |
| `shell` | Open an interactive shell inside the container. |
| `run <command> [args...]` | Run an arbitrary command inside the container. |
| `opencode [args...]` | Run [opencode](https://opencode.ai/) inside the container. |
| `claude [args...]` | Run [claude](https://claude.ai/) inside the container. |
| `target <name> [args...]` | Run a predefined target command from the `targets` section of `config.yaml` inside the container. |
| `clean` | Remove dev-isolator images. |
| `list-base` | List the base image definitions bundled with dev-isolator. |
| `list-targets` | List the targets of the active `config.yaml`. |

Without an explicit subcommand, `shell` runs with the default shell (`bash`).
`dev-isolator help` (or `-h` on any subcommand) prints the help. `opencode`,
`claude`, `run` and `target` take only positional arguments and pass all
following (unknown) arguments through to the command inside the container;
their own options are not parsed by dev-isolator.

### `init`

```bash
dev-isolator init
dev-isolator init -b fedora:43    # base the new Containerfile on Fedora 43
dev-isolator --config gpu init    # initialize the "gpu" sub-configuration
```

Initializes the local configuration of the current project (see
[Typical workflow](README.md#typical-workflow)): it creates the `.devisolator` directory
and fills it with the `Containerfile` and `config.yaml` bundled with
dev-isolator:

- `config.yaml` enables the bundled `opencode` and `claude` addons and
  declares no extra `environment`/`volumes` settings; edit it to your needs
  (see [Project configuration](project-configuration.md#project-configuration)).
- The `Containerfile`'s single `FROM` instruction references a base image from
  which the dev-isolator base image is built. By default that is `ubuntu:26.04`;
  with `--base`/`-b` a different bundled base image can be selected — pass a
  plain base name such as `ubuntu:24.04`, exactly as printed by `list-base`
  (see [Base images](base-images.md#base-images)).

Existing files are kept and reported; pass `--force`/`-f` to overwrite them.
Like `list-base`, `init` needs no pre-existing `.devisolator` directory, so it
can be run in a fresh checkout; an unknown base image aborts with an error
(exit code 255).

With `--config <name>`, `init` creates the sub-configuration directory
`.devisolator/<name>/` instead (see
[Sub-configurations (`--config`)](sub-configurations.md#sub-configurations---config)) and writes the
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
  `dev-isolator-<name>:<tag>-<hash>` (see [Base images](base-images.md#base-images)). Being
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
[Base images](base-images.md#base-images)).

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
   [Project configuration](project-configuration.md#project-configuration) and
   [Run arguments (`run_arguments`)](project-configuration.md#run-arguments-run_arguments)).
4. Your terminal attaches to a `podman exec -it` session running the shell
   (`bash` by default); leave the session with `exit` or Ctrl-D.
5. After the session ends, the container is stopped and removed.

The global `--shell`/`-s` option (see [Command line overview](usage.md#command-line-overview))
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

### `target`

```bash
dev-isolator target test           # run the "test" target from config.yaml
dev-isolator target cover src      # pass "src" to the target's command
```

Runs a predefined target command inside the container (a bit like a predefined
`run`).  Like `run`, `target` takes only positional arguments: the first one
names the target, all following ones are passed to the target's command.

The targets are defined in the `targets` section of the project's `config.yaml`;
each command is a [jinja2](https://jinja.palletsprojects.com/) template in which
the extra arguments given after the target name are available:

```yaml
version: 0.1

targets:
  test: pytest -v {{ all_args }}           # all extra arguments, joined by spaces
  cover: pytest --cov={{ args[0] }}        # the first extra argument
  full:                                    # a whole script, line by line
    - echo "Building the project"
    - pytest -v {{ all_args }}
    - ruff check src
  lint:
    - ruff check
    - "{{ args[0] | optional }}"           # optional argument
```

- `{{ args[0] }}`, `{{ args[1] }}`, ... refer to the individual extra arguments;
  `{{ all_args }}` to all of them joined with spaces.
- A placeholder without the `optional` filter is **mandatory**: running the
  target without that argument aborts with an error.
- A placeholder marked with the `optional` filter is only used when the
  argument was given on the command line; the token holding it is dropped
  otherwise — together with a preceding option token, so an optional
  `-k {{ args[0] | optional }}` pair disappears as a whole. A line that
  becomes empty afterwards is skipped.
- The command may be a single command line (a string, or a one-entry list) or
  a **script given line by line in a list**: every entry is one command line.
  A multi-line script runs line by line in one shell session (the shell
  selected with `--shell/-s`, `bash` by default), which **stops at the first
  failing line**; the target's exit code is that line's.
- Shell operators and redirections (for example `2>&1`, `|`, `&&`, `;`,
  `>>out.txt`) are interpreted: a line containing them is run through the
  shell, so `test: pytest -v 2>&1 | tee output.txt` works as written. Its
  ordinary tokens (and the extra arguments) are quoted safely; operator
  tokens are left as is.
- An unknown target (or no target at all) aborts with an error listing the
  available targets.

The rendered command runs inside the container exactly like `run` (the command
must exist inside the image); the session exit code is returned and the
container is stopped and removed afterwards.

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
[`--config`](sub-configurations.md#sub-configurations---config) is in effect — the selected
sub-configuration's image. `clean --all` and `clean --local` remove images
regardless of the selection.

Before removal, all matching containers are stopped (killed after 10 seconds) and
removed. A container matches when it was created from one of the images or its
name starts with the project's container name prefix (`dev-isolator-<slug>--<tag>`
for `clean`, `dev-isolator-<slug>` for `clean --local`, `dev-isolator-` for
`clean --all`). With `--verbose` the removed containers and images are printed.

Base images (`dev-isolator-<name>:<tag>-<hash>`, e.g.
`dev-isolator-ubuntu:24.04-<hash>`) are never removed by `clean`; see
[Base image cleanup](base-images.md#base-image-cleanup).

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
[`Containerfile`](base-images.md#base-images) can use in its `FROM` instruction (and the
`--base`/`-b` option of [`init`](usage.md#init) accepts), resolved to the matching
bundled base Containerfile (see [Base images](base-images.md#base-images)). Unlike the other
subcommands, `list-base` needs no initialized `.devisolator` directory and
works from any location.

### `list-targets`

```bash
dev-isolator list-targets
```

Lists the target names defined in the `targets` section of the active
`config.yaml`, one per line, sorted alphabetically. These are the targets a
project can run with [`target <name>`](#target). Like `list-base`, it prints
only names (scriptable); it needs the project's `config.yaml`, so it uses the
same project-directory discovery as the other subcommands.

### Discovery of the project directory

The project directory holds the `.devisolator` folder and is found by searching
for a `.devisolator` directory from the current working directory and walking up
the directory tree (to the home directory, or the filesystem root for working
directories outside the home tree); the first hit wins. If none is found, the
`Containerfile` and configuration bundled with dev-isolator are used.

#### `--config`: selecting a sub-configuration

Sub-configurations are selected from this project directory with
`--config <name>` or the `DEVISOLATOR_CONFIG` environment variable; see
[Sub-configurations (`--config`)](sub-configurations.md#sub-configurations---config).