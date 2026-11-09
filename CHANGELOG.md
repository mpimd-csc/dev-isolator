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
* The new ``target`` subcommand runs a predefined target command from
  the ``targets`` section of ``config.yaml`` inside the container (a
  bit like a predefined ``run``).  It takes only positional arguments:
  the first one names the target, all following ones are passed to the
  target's command, which is a jinja2 template in which they are
  available as ``args[0]``, ``args[1]``, ... and as ``all_args`` (all
  of them joined with spaces).  A placeholder marked with the
  ``optional`` filter is only used when the argument was given on the
  command line (the token holding it, and a preceding option token,
  is dropped otherwise); without the filter, the argument is
  mandatory.  The command may be a single command line (a string) or
  a script given line by line in a list; the script lines run line by
  line in one shell session, which stops at the first failing line.
* The new ``list-targets`` subcommand prints the target names defined
  in the ``targets`` section of the active ``config.yaml``, one per
  line, sorted alphabetically.
* Add WaitSpinner
* The merged Containerfile is written as a temporary file into the
  build context and removed afterwards, so podman-py does not copy it
  itself and leave stale ``.containerfile.*`` artifacts in the
  project directory.
* The README documents how to install the tool with ``pipx`` which
  provides both ``dev-isolator`` and ``di`` on the ``PATH``.
