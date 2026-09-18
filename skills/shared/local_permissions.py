"""Creation mode for persistent local library directories."""
import os

# Windows interprets 0700 as a protected owner-only DACL. Inherit the already
# selected parent ACL so the user's account and other authorized tasks can read
# the library. On POSIX retain the existing owner-only directory mode.
LIBRARY_DIRECTORY_MODE = 0o777 if os.name == "nt" else 0o700
