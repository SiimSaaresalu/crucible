"""Built-in environments, one subpackage each.

Each environment is a package with its task, its own `policies/`, `tests/` and a `README.md`
(the environment card). The registry finds environments through the `crucible.envs` entry point
group, so these built-ins and third-party plugins load the same way; nothing here imports them.
"""
