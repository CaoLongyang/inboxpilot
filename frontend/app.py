"""InboxPilot frontend (Streamlit) — placeholder.

Owner: B. This file only wires the app to the backend contract so the two sides
can be developed in parallel from Day 1.

Run:
    streamlit run frontend/app.py
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE_URL = os.getenv("INBOXPILOT_API_URL", "http://localhost:8000")

st.set_page_config(page_title="InboxPilot", page_icon="📥", layout="wide")

st.title("InboxPilot")
st.caption("From email information to real-world action.")

st.subheader("Backend status")

try:
    health = httpx.get(f"{API_BASE_URL}/health", timeout=3.0).json()
    if health.get("status") == "ok":
        st.success("Backend connected")
    else:
        st.warning("Backend reachable, but integrations are not configured yet")
    st.json(health.get("integrations", {}))
except httpx.HTTPError as exc:
    st.error(f"Cannot reach the backend at {API_BASE_URL}: {exc}")

st.divider()
st.info(
    "Screens to build (B1): Connect → Inbox → Analysis → Action Review.\n\n"
    "All data shapes are defined in `backend/schemas.py`. Do not redefine them here."
)
