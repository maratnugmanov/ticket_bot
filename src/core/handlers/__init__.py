from __future__ import annotations

# This file makes the 'handlers' directory a Python package.
# The imports below are crucial. They ensure that the decorated route
# handlers in each file are registered with the central router when
# the 'handlers' package is imported.

from . import device_handlers
from . import ticket_handlers
from . import user_handlers
