Version 0.1
===========

* Initial Release, playground for the idea.
* The ``addon.*`` files are jinja2 templates (``addon.<name>.jinja2``).
* The new ``opencode`` subcommand runs opencode inside the container
  and passes all following arguments (for example
  ``dev-isolator opencode --port 4096``) verbatim to it. Global
  verbosity flags work before the subcommand
  (``dev-isolator -vvv opencode``).
