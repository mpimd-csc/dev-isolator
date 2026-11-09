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
- [Usage](docs/usage.md#usage)
  - [Command line overview](docs/usage.md#command-line-overview)
  - [`init`](docs/usage.md#init)
  - [`build`](docs/usage.md#build)
  - [`shell`](docs/usage.md#shell)
  - [`run`, `opencode`, `claude`](docs/usage.md#run-opencode-claude)
  - [`clean`](docs/usage.md#clean)
  - [`list-base`](docs/usage.md#list-base)
  - [Discovery of the project directory](docs/usage.md#discovery-of-the-project-directory)
- [Sub-configurations (`--config`)](docs/sub-configurations.md#sub-configurations---config)
  - [What a sub-configuration overrides](docs/sub-configurations.md#what-a-sub-configuration-overrides)
  - [Naming: every sub-configuration gets its own image](docs/sub-configurations.md#naming-every-sub-configuration-gets-its-own-image)
  - [Example: a project with a `gpu` sub-configuration](docs/sub-configurations.md#example-a-project-with-a-gpu-sub-configuration)
- [Project configuration](docs/project-configuration.md#project-configuration)
  - [Addon configuration (`config.<addon>.yaml`)](docs/project-configuration.md#addon-configuration-configaddonyaml)
  - [Run arguments (`run_arguments`)](docs/project-configuration.md#run-arguments-run_arguments)
- [Base images](docs/base-images.md#base-images)
  - [What a project `Containerfile`'s `FROM` references](docs/base-images.md#what-a-project-containerfiles-from-references)
  - [Where a base image comes from](docs/base-images.md#where-a-base-image-comes-from)
  - [What goes into the base image, what goes into the project image](docs/base-images.md#what-goes-into-the-base-image-what-goes-into-the-project-image)
  - [Base image cleanup](docs/base-images.md#base-image-cleanup)
  - [Bundle base images and addons](docs/base-images.md#bundle-base-images-and-addons)
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
[Project configuration](docs/project-configuration.md#project-configuration) and
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
[Base images](docs/base-images.md#base-images)); that base image provides the host user with your
UID/GID (so `useradd` and `getent` must exist there); the project build always
ends with `USER <you>` and `WORKDIR /workspace`.

### What gets built

For the folder above, dev-isolator builds two images:

1. the shared base image `dev-isolator-ubuntu:24.04` (the actual tag carries a
   content hash, see [Base images](docs/base-images.md#base-images)) — the bundled base Containerfile,
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
