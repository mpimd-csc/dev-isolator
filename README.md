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
