"""Pure functions from a stored run to numbers worth looking at.

Nothing here touches the file system or the store: every analyzer takes a
:class:`~longpole.model.Run` and returns a frozen result. The command layer
loads the run, the exporters render the result.
"""

from __future__ import annotations
