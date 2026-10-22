"""Typed private reexports of the existing no-follow OS operations; no policy."""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from ._artifact_cache_windows import Handle as Directory
    from ._artifact_cache_windows import (
        close as close,
    )
    from ._artifact_cache_windows import (
        flush as flush,
    )
    from ._artifact_cache_windows import (
        listdir as listdir,
    )
    from ._artifact_cache_windows import (
        mkdir as mkdir,
    )
    from ._artifact_cache_windows import (
        open_directory as open_directory,
    )
    from ._artifact_cache_windows import (
        open_root as open_root,
    )
    from ._artifact_cache_windows import (
        publish as publish,
    )
    from ._artifact_cache_windows import (
        read_file as read_file,
    )
    from ._artifact_cache_windows import (
        remove_tree as remove_tree,
    )
    from ._artifact_cache_windows import (
        write_file as write_file,
    )
else:
    from ._artifact_cache_fs import (
        Directory as Directory,
    )
    from ._artifact_cache_fs import (
        close as close,
    )
    from ._artifact_cache_fs import (
        flush as flush,
    )
    from ._artifact_cache_fs import (
        listdir as listdir,
    )
    from ._artifact_cache_fs import (
        mkdir as mkdir,
    )
    from ._artifact_cache_fs import (
        open_directory as open_directory,
    )
    from ._artifact_cache_fs import (
        open_root as open_root,
    )
    from ._artifact_cache_fs import (
        publish as publish,
    )
    from ._artifact_cache_fs import (
        read_file as read_file,
    )
    from ._artifact_cache_fs import (
        remove_tree as remove_tree,
    )
    from ._artifact_cache_fs import (
        write_file as write_file,
    )

__all__ = [
    "Directory", "close", "flush", "listdir", "mkdir", "open_directory", "open_root",
    "publish", "read_file", "remove_tree", "write_file",
]
