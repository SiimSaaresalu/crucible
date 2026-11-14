"""Built-in policies. Importing a module registers its policies."""

from nightshift.policies import baselines
from nightshift.policies.reference import guess

__all__ = ["baselines", "guess"]
