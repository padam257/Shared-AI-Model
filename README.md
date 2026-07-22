# Shared AI Mode Chargeback PoC - Streamlit + Azure AI Foundry

This PoC demonstrates BU-wise chargeback for a shared LLM deployment by capturing token usage per authenticated user/BU.

## What it does

- Provides a Streamlit chat UI.
- Calls an Azure AI Foundry / Azure OpenAI chat model deployment, for example `gpt-5.4`.
- Resolves user identity from Azure App Service Authentication headers.
- Maps Entra ID security group object IDs to BU chargeback codes.
- Passes a pseudonymous BU/user identifier in the OpenAI `user` request field.
- Logs prompt/completion/total token usage per call.
- Shows an in-app BU-wise token usage dashboard and CSV export.

## File structure

```text
.
├── app.py
├── usage_store.py
├── requirements.txt
├── startup.sh
├── .env.sample
├── .streamlit/config.toml
└── .github/workflows/deploy-webapp.yml
```

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env
# edit .env with endpoint, deployment, key or managed identity flag
streamlit run app.py
```

## Required Azure App Settings

Add these under **App Service > Settings > Environment variables**:

```bash
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com/openai/v1/
AZURE_OPENAI_DEPLOYMENT=gpt-5.4
AZURE_OPENAI_API_KEY=<key if not using managed identity>
USE_MANAGED_IDENTITY=false
BU_GROUP_MAP_JSON={"<group-object-id-1>":"BU-FINANCE","<group-object-id-2>":"BU-HR"}
DEFAULT_BU_CODE=BU-UNKNOWN
SCM_DO_BUILD_DURING_DEPLOYMENT=true
```

For managed identity, set `USE_MANAGED_IDENTITY=true`, remove `AZURE_OPENAI_API_KEY`, enable a system-assigned identity on the Web App, and grant the identity the right Azure AI/Azure OpenAI RBAC role on the model resource.

## Azure CLI deployment setup

```bash
RG=rg-ai-chargeback-poc
LOC=eastus
PLAN=asp-ai-chargeback-poc
APP=<globally-unique-webapp-name>

az group create -n $RG -l $LOC
az appservice plan create -g $RG -n $PLAN --is-linux --sku B1
az webapp create -g $RG -p $PLAN -n $APP --runtime "PYTHON|3.14"
az webapp config set -g $RG -n $APP --startup-file "bash startup.sh"
az webapp config appsettings set -g $RG -n $APP --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true
```

Then configure app settings:

```bash
az webapp config appsettings set -g $RG -n $APP --settings \
  AZURE_OPENAI_ENDPOINT="https://<your-resource-name>.openai.azure.com/openai/v1/" \
  AZURE_OPENAI_DEPLOYMENT="gpt-5.4" \
  AZURE_OPENAI_API_KEY="<key>" \
  USE_MANAGED_IDENTITY="false" \
  DEFAULT_BU_CODE="BU-UNKNOWN" \
  BU_GROUP_MAP_JSON='{"11111111-1111-1111-1111-111111111111":"BU-FINANCE","22222222-2222-2222-2222-222222222222":"BU-HR"}'
```

## Enable Entra authentication on App Service

1. Go to **App Service > Authentication**.
2. Add identity provider: **Microsoft**.
3. Restrict access to authenticated users.
4. Configure the app registration/token claims so security group IDs are emitted, or use app roles/Graph for production.
5. Add users to BU security groups.

## GitHub Actions deployment

1. Put this project in a GitHub repo.
2. In Azure Portal, download the Web App publish profile from **Overview > Get publish profile**.
3. In GitHub repo, add secret: `AZURE_WEBAPP_PUBLISH_PROFILE`.
4. Add repo variables:
   - `AZURE_WEBAPP_NAME` = your Web App name.
5. Push to `main`.

## Important production notes

- The local JSONL log is only for PoC. For enterprise chargeback, send usage events to Log Analytics / Application Insights / Event Hub / ADX / Storage Table / Cosmos DB.
- App Service local files may not be suitable as chargeback system of record, especially with scale-out.
- Do not let the client choose BU. Resolve BU server-side from trusted Entra ID claims.
- If Entra group overage occurs, group IDs may not be present in the token; query Microsoft Graph or use app roles.
