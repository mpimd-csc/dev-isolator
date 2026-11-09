## Examples: a `.devisolator` folder

## Contents

- [What gets built](#what-gets-built)

This section walks through a complete `.devisolator` folder for a small Python
project. Put the folder in the root of your project; the file types and how
`environment`/`volumes` are processed are covered in
[Project configuration](project-configuration.md#project-configuration) and
[Addon configuration](#addon-configuration-configaddonyaml).

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
[Base images](base-images.md#base-images)); that base image provides the host user with your
UID/GID (so `useradd` and `getent` must exist there); the project build always
ends with `USER <you>` and `WORKDIR /workspace`.

### What gets built

For the folder above, dev-isolator builds two images:

1. the shared base image `dev-isolator-ubuntu:24.04` (the actual tag carries a
   content hash, see [Base images](base-images.md#base-images)) — the bundled base Containerfile,
   the host-user block (`USER <you>`) and the enabled addon templates (`addon.python.jinja2`,
   then the bundled `addon.opencode.jinja2`), built once and reused;
2. the project image `dev-isolator:<slug_of_my-project>--default` (for the path
   above `dev-isolator:home-user-projects-my-project--default`) — your
   `Containerfile` followed by `USER <you>` and `WORKDIR /workspace`.

Running `dev-isolator` then mounts `my-project/` at `/workspace` inside a container
running as your own UID/GID, so files you create stay owned by you.
