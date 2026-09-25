#!/bin/sh
# Grants the Function identity Fabric access, applies Warehouse objects, and reminds
# the operator to seed the GitHub credential.
# Invoked automatically by `azd up` via the postprovision hook.
set -e

: "${FABRIC_SQL_ENDPOINT:?FABRIC_SQL_ENDPOINT is required.}"
: "${FABRIC_WAREHOUSE_NAME:?FABRIC_WAREHOUSE_NAME is required.}"
: "${FABRIC_WORKSPACE_ID:?FABRIC_WORKSPACE_ID is required.}"

if ! command -v sqlcmd >/dev/null 2>&1; then
    echo "sqlcmd not found. Install SQL Server command line tools and rerun azd provision." >&2
    exit 1
fi

if [ -n "$FUNCTION_PRINCIPAL_ID" ]; then
    role_assignments_url="https://api.fabric.microsoft.com/v1/workspaces/$FABRIC_WORKSPACE_ID/roleAssignments"
    existing_role=$(az rest \
        --method get \
        --url "$role_assignments_url" \
        --resource "https://api.fabric.microsoft.com" \
        --query "value[?principal.id=='$FUNCTION_PRINCIPAL_ID'] | [0].id" \
        --output tsv)

    if [ -z "$existing_role" ]; then
        echo "Granting Fabric workspace Viewer access to $FUNCTION_APP_NAME ..."
        az rest \
            --method post \
            --url "$role_assignments_url" \
            --resource "https://api.fabric.microsoft.com" \
            --headers "Content-Type=application/json" \
            --body "{\"principal\":{\"id\":\"$FUNCTION_PRINCIPAL_ID\",\"type\":\"ServicePrincipal\"},\"role\":\"Viewer\"}" \
            --output none
        sleep 10
    fi
fi

for script in sql/01_schema.sql sql/02_staging.sql sql/03_procedures.sql sql/04_views.sql sql/06_security.sql; do
    echo "Applying $script ..."
    # -G with no username uses the current Azure CLI / Entra identity.
    sqlcmd -S "$FABRIC_SQL_ENDPOINT" -d "$FABRIC_WAREHOUSE_NAME" -G -b -i "$script"
done

if [ -n "$FUNCTION_APP_NAME" ]; then
    echo "Granting Warehouse access to $FUNCTION_APP_NAME ..."
    tmp=$(mktemp)
    sed "s/<FUNCTION_APP_NAME>/$FUNCTION_APP_NAME/g" sql/05_grants.sql > "$tmp"
    sqlcmd -S "$FABRIC_SQL_ENDPOINT" -d "$FABRIC_WAREHOUSE_NAME" -G -b -i "$tmp"
    rm -f "$tmp"
fi

echo ""
echo "Fabric Warehouse ready. Final manual step - store your GitHub credential:"
echo "  az keyvault secret set --vault-name $AZURE_KEY_VAULT_NAME --name github-credential --value <PAT-or-app-private-key>"
