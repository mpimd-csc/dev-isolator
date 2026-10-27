Version 0.1
===========

* Initial Release, playground for the idea.
* The container name is suffixed with 8 random characters, so several
  containers of the same project can run in parallel.
* The ``clean`` subcommand now also stops, kills and removes the
  containers matching the removed images (by image id or by container
  name prefix), even when the image was already deleted.
* The ``addon.*`` files are jinja2 templates (``addon.<name>.jinja2``).
* The new ``opencode`` subcommand runs opencode inside the container
  and passes all following arguments (for example
  ``dev-isolator opencode --port 4096``) verbatim to it. Global
  verbosity flags work before the subcommand
  (``dev-isolator -vvv opencode``).
* Add WaitSpinner
* The merged Containerfile is written as a temporary file into the
  build context and removed afterwards, so podman-py does not copy it
  itself and leave stale ``.containerfile.*`` artifacts in the
  project directory.
