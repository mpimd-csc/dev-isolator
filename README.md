# dev-isolator

`dev-isolator` is a command line tool that manages a per-project,
[podman](https://podman.io/) based development environment.

## Licensing

Copyright (C) 2026 Martin Köhler

`dev-isolator` is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version (GPL-3.0-or-later).

See [LICENSE](LICENSE) for details.

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

### Requirements 

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


## Quickstart 

Using the default config just change to the workspace of your project and 
run 
```bash 
di 
```
After the images are built, you get a shell in an isolated Ubuntu 26.04 
environment, with a minimal developement and Python toolset installed. Your 
current directory is available in `/workspace`. 

If you want to run [Opencode](https://opencode.ai/) in the current directory, just run 
```bash 
di opencode
```
and it sets up an Opencode session in the current directory, but isolated from 
your host. In addition to the current working directory, it gets access your 
Opencode configuration from `~/.config/opencode`.

In the same way, running 
```
di claude
```
gives you access to the Claude CLI agent. 

### Customizing the Environemnt

If you need to environment, e.g. using `ubuntu:24.04` instead of `ubuntu:26.04`
you can initialize your own project configuration using 
```bash 
di init -b ubuntu:24.04
```
and edit `.devisolator/Containerfile` to your needs. Afterwards, running 
```bash 
di 
``` 
will rebuild your container. 

Futher options and usage information are available in the [documentation](#documentation)

## Documentation

Further documentation lives in the `docs/` directory:

- [Usage](./docs/usage.md) — subcommands and command line options
- [Sub-configurations (`--config`)](./docs/sub-configurations.md)
- [Project configuration](./docs/project-configuration.md)
- [Base images](./docs/base-images.md)
- [Examples: a `.devisolator` folder](./docs/examples.md)


## Contributing

Contributions are welcome from everyone; please follow the guide in
[CONTRIBUTING.md](CONTRIBUTING.md).


