## Sub-configurations (`--config`)

## Contents

- [What a sub-configuration overrides](#what-a-sub-configuration-overrides)
- [Naming: every sub-configuration gets its own image](#naming-every-sub-configuration-gets-its-own-image)
- [Example: a project with a `gpu` sub-configuration](#example-a-project-with-a-gpu-sub-configuration)

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

### What a sub-configuration overrides

For a sub-configuration `<name>`, the `Containerfile` and the `config.yaml` are
looked up in this order, and the *first found file wins* (the files are never
merged):

1. `.devisolator/<name>/` — the sub-configuration itself.
2. `.devisolator/` — the project's default configuration.
3. The files bundled with dev-isolator.

A sub-configuration is created with `dev-isolator --config <name> init`, which
writes the bundled `Containerfile` (rewritten to the requested `--base` image)
and `config.yaml` directly into `.devisolator/<name>/` (see
[`init`](usage.md#init)) — everything the directory does not contain then falls back
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

### Naming: every sub-configuration gets its own image

The sub-configuration name doubles as the image tag. Building under `--config gpu`
produces the image `dev-isolator:<slug_of_the_project>--gpu` (with the default
configuration the tag is `default`), and the container is created as
`dev-isolator-<slug_of_the_project>--gpu`. Several sub-configurations of a project
can therefore be built and kept side by side, and `build`, `shell`, `run`,
`opencode` and `claude` always act on the currently selected one.
[`clean`](usage.md#clean) removes the selected configuration's image; use
`clean --local` to drop every tag variant of the project at once.

### Example: a project with a `gpu` sub-configuration

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