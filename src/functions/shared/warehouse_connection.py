"""Resilient ODBC connections to Microsoft Fabric Warehouse."""
from __future__ import annotations

import pyodbc
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


@retry(
    retry=retry_if_exception_type(pyodbc.Error),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    reraise=True,
)
def connect(connection_string: str, *, autocommit: bool = False) -> pyodbc.Connection:
    connection = pyodbc.connect(connection_string, timeout=60)
    connection.autocommit = autocommit
    return connection
