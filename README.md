# dev-isolator

This project contains a template for Python based command line applications.
If you want to start new project, you have to fork this project and remove
its fork connection to this project.
Afterwards, edit the following files to get started:

## 1. Rename the project.

Renaming can be done easily with the `rename.sh` script. Just run it
using
```shell
bash rename.sh new-project-name
rm rename.sh
```
The new project name must only contain the characters from `a` to `z`, numbers,
hyphens, or underscores. Hyphens are replaced by underscores, where Python SLUGs
are required. For example this project is named `dev-isolator` but
the import name for python is 'dev_isolator.'

# 2. Check Licenses, README

The default licenses is GPL3 or later. Just check the [LICENSE](LICENSE) if you are fine
with it or replace it by your desired one.

Furthermore, you have to adjust this README.md file.

The following files are optional but nice to have:

* CHANGELOG.md
* CITATION.cff
* CONTRIBUTING.md
* CONTRIBUTORS.md

if you do not need them, remove them.

# 3. Start coding

Now, this is your task.


# Common information in the README

## Installation

```bash
pip install dev-isolator
```

Or for development:

```bash
pip install -e .
```

## Usage

### CLI

```bash
dev-isolator

# or
python -m dev_isolator
```

### Project configuration

The configuration of a containerized dev environment lives in the
`.devisolator` directory of your project:

- ``Containerfile``: The base image definition.
- ``config.yaml``: Names the enabled addons:

  ```yaml
  addons_enabled:
    - opencode
  ```

- ``addon.<name>.jinja2``: A jinja2 template with the Containerfile
  instructions appended for the addon `<name>`. It receives the
  variables ``uid``, ``gid``, ``username`` and ``home_dir`` of the
  invoking host user.
- ``config.<addon>.yaml``: Runtime settings an addon requests for
  its container (see below).

#### Addon configuration (`config.<addon>.yaml`)

An addon config file has the shape

```yaml
environment:
  - VAR1
volumes:
  - /host/path:/path/in/container
  - ENV[DEV_DATA_DIR]/data:/data:ro
```

- ``environment`` lists the names of environment variables taken
  from the host and passed into the container. Variables which are
  not set on the host are skipped with a message.
- ``volumes`` lists ``host:container[:mode]`` specs. The mode
  defaults to ``rw`` when omitted (``ro`` mounts read-only).
  Each spec may contain ``ENV[VAR]``, ``~`` and ``${VAR}``
  references. They are expanded in the following order, before the
  spec is parsed:

  1. ``ENV[VAR]`` is replaced by the value of the environment
     variable ``VAR``. This happens first, so the value may itself
     contain the ``:`` separators of the spec. If the variable is
     not set, an error is raised.
  2. ``~`` is expanded to the home directory and ``${VAR}`` to the
     value of ``VAR``. Unset variables expand to an empty string.

