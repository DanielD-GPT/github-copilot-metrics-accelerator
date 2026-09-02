#!/bin/sh
# Applies the SQL objects and reminds the operator to seed the GitHub credential.
# Invoked automatically by `azd up` via the postprovision hook.
set -e

if [ -z "$SQL_SERVER_FQDN" ]; then
    echo "SQL_SERVER_FQDN not set; skipping database setup."
    exit 0
fi

if ! command -v sqlcmd >/dev/null 2>&1; then
    echo "sqlcmd not found. Install SQL Server command line tools, then apply sql/*.sql manually."
    exit 0
fi

for script in sql/01_schema.sql sql/02_staging.sql sql/03_procedures.sql sql/04_views.sql sql/06_security.sql; do
    echo "Applying $script ..."
    # -G with no username uses the current Azure CLI / Entra identity.
    sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b -i "$script"
done

if [ -n "$FUNCTION_APP_NAME" ]; then
    echo "Granting database access to $FUNCTION_APP_NAME ..."
    tmp=$(mktemp)
    sed "s/<FUNCTION_APP_NAME>/$FUNCTION_APP_NAME/g" sql/05_grants.sql > "$tmp"
    sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b -i "$tmp"
    rm -f "$tmp"
fi

echo ""
echo "Database ready. Final manual step - store your GitHub credential:"
echo "  az keyvault secret set --vault-name $AZURE_KEY_VAULT_NAME --name github-credential --value <PAT-or-app-private-key>"
