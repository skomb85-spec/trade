"""Append CSVs dropped into the repo to an existing Google Sheet.

The collector (Grok, or anything else) pushes a CSV into ``x_inbox/``;
GitHub Actions runs ``python -m x_inbox`` and the rows land in the sheet.
No X/Twitter credentials are involved -- only a Google service account.
"""

from .core import (  # noqa: F401
    AppendPlan,
    Config,
    ConfigError,
    CredentialsError,
    archive_files,
    existing_keys,
    find_inbox_files,
    load_config,
    load_credentials_info,
    plan_append,
    read_csv_rows,
    resolve_header,
    row_key,
)
