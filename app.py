import base64
import hashlib
import json
import os
import time
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from usage_store import write_usage_event, read_usage_events, export_usage_csv

load_dotenv()

APP_TITLE = "Shared AI Model Chargeback PoC"
SYSTEM_PROMPT = (
    "You are an enterprise assistant. Be concise, safe, and useful. "
    "Do not reveal secrets or internal identifiers."
)


def get_headers() -> Dict[str, str]:
    """Return request headers when deployed behind App Service Authentication.

    Streamlit exposes headers in st.context.headers in recent versions. For local runs,
    this returns an empty dict and a BU can be selected from the sidebar.
    """
    try:
        headers = dict(st.context.headers)  # type: ignore[attr-defined]
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def decode_client_principal(headers: Dict[str, str]) -> Dict:
    encoded = headers.get("x-ms-client-principal")
    if not encoded:
        return {"claims": []}
    try:
        # App Service sends Base64 JSON. Padding may be omitted by some proxies.
        padded = encoded + "=" * (-len(encoded) % 4)
        raw = base64.b64decode(padded).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return {"claims": []}


def claims_to_dict(principal: Dict) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for claim in principal.get("claims", []):
        typ = claim.get("typ") or claim.get("type")
        val = claim.get("val") or claim.get("value")
        if typ and val:
            result.setdefault(str(typ), []).append(str(val))
    return result


def load_bu_group_map() -> Dict[str, str]:
    raw = os.getenv("BU_GROUP_MAP_JSON", "{}")
    try:
        return {str(k).lower(): str(v) for k, v in json.loads(raw).items()}
    except Exception:
        return {}


def resolve_identity_and_bu() -> Tuple[str, str, List[str], bool]:
    """Resolve user and BU from App Service EasyAuth headers.

    Returns: user_name, bu_code, matched_group_ids, is_authenticated
    """
    headers = get_headers()
    user_name = headers.get("x-ms-client-principal-name") or "local.user@contoso.com"
    principal = decode_client_principal(headers)
    claims = claims_to_dict(principal)

    # Common group claim names can vary based on token configuration/claim mapping.
    group_values: List[str] = []
    for key, vals in claims.items():
        lk = key.lower()
        if lk in {"groups", "group", "roles"} or lk.endswith("/groups") or lk.endswith("/role"):
            group_values.extend(vals)

    # App Service may also expose role claims; for PoC allow group/object IDs only.
    bu_map = load_bu_group_map()
    matched = [g for g in group_values if g.lower() in bu_map]
    if matched:
        bu_code = bu_map[matched[0].lower()]
    else:
        bu_code = os.getenv("DEFAULT_BU_CODE", "BU-UNKNOWN")

    authenticated = bool(headers.get("x-ms-client-principal-id") or headers.get("x-ms-client-principal"))
    return user_name, bu_code, matched, authenticated


def user_hash(user_name: str) -> str:
    salt = os.getenv("USER_HASH_SALT", "poc-salt-change-me")
    return hashlib.sha256(f"{salt}:{user_name}".encode("utf-8")).hexdigest()[:16]


def build_openai_client() -> AzureOpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")

    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured.")

    api_key = os.getenv("AZURE_OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY is not configured."
        )

    api_version = os.getenv(
        "AZURE_OPENAI_API_VERSION",
        "2024-12-01-preview"
    )

    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


def call_llm(messages: List[Dict[str, str]], bu_code: str, hashed_user: str) -> Tuple[str, Dict, int, str]:
    client = build_openai_client()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4")
    started = time.perf_counter()

    # The 'user' value gives a traceable end-user/BU identifier to the model API request.
    # Chargeback source of truth remains the app-side event log written below.
    response = client.chat.completions.create(
        model=deployment,
        messages=messages,
        max_completion_tokens=int(os.getenv("AZURE_OPENAI_MAX_COMPLETION_TOKENS", "4096")),
        user=f"{bu_code}:{hashed_user}",
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    answer = response.choices[0].message.content or ""
    usage = response.usage.model_dump() if response.usage else {}
    return answer, usage, latency_ms, response.id


def show_usage_dashboard():
    st.subheader("PoC chargeback dashboard")
    events = read_usage_events()
    if not events:
        st.info("No usage events logged yet.")
        return
    df = pd.DataFrame(events)
    expected_cols = ["timestamp_utc", "bu_code", "user_hash", "deployment", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None

    summary = (
        df.groupby("bu_code", dropna=False)[["prompt_tokens", "completion_tokens", "total_tokens"]]
        .sum()
        .reset_index()
        .sort_values("total_tokens", ascending=False)
    )
    st.dataframe(summary, use_container_width=True)
    st.bar_chart(summary.set_index("bu_code")["total_tokens"])

    st.caption("Recent usage events")
    st.dataframe(df[expected_cols].tail(50).sort_index(ascending=False), use_container_width=True)
    csv_path = export_usage_csv()
    with open(csv_path, "rb") as f:
        st.download_button("Download usage CSV", f, file_name="usage_summary.csv", mime="text/csv")


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Demonstrates token-based BU chargeback for a shared Azure AI Foundry / Azure OpenAI deployment.")

    user_name, bu_code, matched_groups, authenticated = resolve_identity_and_bu()
    hashed_user = user_hash(user_name)

    with st.sidebar:
        st.header("Resolved context")
        st.write("Authenticated via App Service EasyAuth:", "Yes" if authenticated else "No/local")
        st.write("User:", user_name)
        st.write("User hash:", hashed_user)
        st.write("BU code:", bu_code)
        st.write("Matched group IDs:", matched_groups or "None")
        if not authenticated:
            local_bu = st.selectbox("Local test BU override", [bu_code, "BU-FINANCE", "BU-HR", "BU-TECH", "BU-UNKNOWN"])
            bu_code = local_bu
        st.divider()
        st.write("Deployment:", os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"))

    tab_chat, tab_usage, tab_design = st.tabs(["Chat", "Usage dashboard", "PoC design"])

    with tab_chat:
        if "messages" not in st.session_state:
            st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        for msg in st.session_state.messages:
            if msg["role"] in ("user", "assistant"):
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

        prompt = st.chat_input("Ask something...")
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Calling Azure AI Foundry model..."):
                    try:
                        answer, usage, latency_ms, request_id = call_llm(st.session_state.messages, bu_code, hashed_user)
                        st.markdown(answer)
                        st.session_state.messages.append({"role": "assistant", "content": answer})

                        event = {
                            "bu_code": bu_code,
                            "user_hash": hashed_user,
                            "deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"),
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                            "latency_ms": latency_ms,
                            "request_id": request_id,
                        }
                        write_usage_event(event)
                        st.caption(f"Usage: {event['prompt_tokens']} prompt + {event['completion_tokens']} completion = {event['total_tokens']} total tokens | BU={bu_code}")
                    except Exception as ex:
                        st.error(f"LLM call failed: {ex}")

    with tab_usage:
        show_usage_dashboard()

    with tab_design:
        st.markdown(
            """
### How chargeback is demonstrated
1. User signs in through Azure App Service Authentication with Microsoft Entra ID.
2. App Service injects identity headers into the backend request.
3. The app decodes `X-MS-CLIENT-PRINCIPAL`, reads group claims, and maps security group object IDs to BU codes using `BU_GROUP_MAP_JSON`.
4. For each LLM call, the app passes a pseudonymous identifier in the API request `user` field: `BU-CODE:user_hash`.
5. The app captures returned token usage: `prompt_tokens`, `completion_tokens`, and `total_tokens`.
6. The app writes one JSONL usage event per call. The dashboard aggregates tokens by BU.

### Production hardening recommendations
- Replace local JSONL with Log Analytics, Application Insights custom events, Event Hub, ADX, Storage Table, or Cosmos DB.
- Use managed identity instead of API keys where supported by your Azure AI endpoint and RBAC model.
- Do not rely only on client-provided BU values. Resolve BU server-side from Entra ID claims or Graph.
- Handle Entra group overage by querying Microsoft Graph if users are members of many groups.
- Add monthly rate cards to convert token usage into INR/USD cost per BU.
            """
        )


if __name__ == "__main__":
    main()
