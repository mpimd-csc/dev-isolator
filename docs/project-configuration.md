## Project configuration

The configuration of a containerized dev environment lives in the `.devisolator`
directory of your project:

- `Containerfile`: the base image definition (see [Base images](base-images.md#base-images)).
- `config.yaml`: the enabled addons and container settings. Besides `addons`, it
  can also declare `environment` variables, additional `volumes`, build options
  and `run_arguments` for the container (see
  [Run arguments (`run_arguments`)](project-configuration.md#run-arguments-run_arguments)):

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
  Containerfile (see [Base images](base-images.md#base-images)) and receives the variables
  `uid`, `gid`, `username` and `home_dir` of the invoking host user.
- `config.<addon>.yaml`: runtime settings an addon requests for its container (see
  [Addon configuration](#addon-configuration-configaddonyaml)).

Configuration resolution rules:

- For `config.yaml` and `config.<addon>.yaml`, strictly the first file found is
  used: the file in `<project>/.devisolator/` if it exists, otherwise the one
  bundled with dev-isolator. Only one is active; the two are never merged.
- The `addons` list names the addons to append to the Containerfile. Without any
  `config.yaml` at all, every `addon.*.jinja2` file is enabled.
- The `environment` and `volumes` sections of `config.yaml` are processed exactly
  like those of an addon config file (see
  [below](#addon-configuration-configaddonyaml)): `environment` entries are either
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

### Addon configuration (`config.<addon>.yaml`)

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

### Run arguments (`run_arguments`)

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