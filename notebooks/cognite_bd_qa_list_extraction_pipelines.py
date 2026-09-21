# Databricks notebook source
# MAGIC %md
# MAGIC # Cognite BD QA — list extraction pipelines
# MAGIC
# MAGIC Connects to CDF project **`bdx-qa`** with an Azure AD service principal (OAuth client credentials) and lists extraction pipelines.
# MAGIC
# MAGIC Fill the widgets (or Databricks secrets) before running. Do not commit client secrets.

# COMMAND ----------

dbutils.widgets.text("cognite_host", "https://az-phx-001.cognitedata.com")
dbutils.widgets.text("cognite_project", "bdx-qa")
dbutils.widgets.text("tenant_id", "")
dbutils.widgets.text("client_id", "")
dbutils.widgets.text("client_secret", "")
dbutils.widgets.text("secret_scope", "https://az-phx-001.cognitedata.com/.default")

# COMMAND ----------

# MAGIC %pip install cognite-sdk --quiet

# COMMAND ----------

from cognite.client import ClientConfig, CogniteClient
from cognite.client.config import global_config
from cognite.client.credentials import OAuthClientCredentials


def _widget(name: str) -> str:
    return dbutils.widgets.get(name).strip()


def _credential(widget_name: str, secret_key: str) -> str:
    scope = _widget("secret_scope")
    if scope:
        return dbutils.secrets.get(scope, secret_key)
    return _widget(widget_name)


cognite_host = _widget("cognite_host").rstrip("/")
cognite_project = _widget("cognite_project")
tenant_id = _credential("tenant_id", "tenant-id")
client_id = _credential("client_id", "client-id")
client_secret = _credential("client_secret", "client-secret")

missing = [
    name
    for name, value in [
        ("cognite_host", cognite_host),
        ("cognite_project", cognite_project),
        ("tenant_id", tenant_id),
        ("client_id", client_id),
        ("client_secret", client_secret),
    ]
    if not value
]
if missing:
    raise ValueError(
        "Missing: "
        + ", ".join(missing)
        + ". Set widgets, or set secret_scope and keys tenant-id / client-id / client-secret."
    )

global_config.disable_pypi_version_check = True

client = CogniteClient(
    ClientConfig(
        client_name="databricks-cognite-bd-qa-extpipes",
        project=cognite_project,
        base_url=cognite_host,
        credentials=OAuthClientCredentials(
            token_url=f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[f"{cognite_host}/.default"],
        ),
    )
)

print(f"Connected to {cognite_host} / project {cognite_project}")

# COMMAND ----------

import pandas as pd

pipelines = client.extraction_pipelines.list(limit=None)
rows = pipelines.dump() if pipelines else []

print(f"Extraction pipelines: {len(rows)}")
display(pd.DataFrame(rows))
