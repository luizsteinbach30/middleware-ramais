"""Single source of truth for the application version.

The release pipeline reads this file and validates it matches the git tag
(`v<__version__>`). The updater compares ``__version__`` of the running process
with the latest GitHub release in the configured channel.
"""

__version__ = "2.8.0"
__channel__ = "stable"
