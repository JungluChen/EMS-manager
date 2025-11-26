import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
import time
import plotly.graph_objects as go
from pathlib import Path


# -----------------------------------------------------------
# Streamlit Page Config
# -----------------------------------------------------------
st.set_page_config(page_title="EMS 管理台", layout="wide")


# -----------------------------------------------------------
# GitHub Download Function
# -----------------------------------------------------------
def gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['GIT_TOKEN']}",
        "Accept": "application/vnd.github+json"
    }


def gh_download_file(path):
    url = f"https://api.github.com/repos/{st.secrets['GIT_OWNER']}/{st.secrets['GIT_REPO']}/contents/{path}?ref={st.secrets['GIT_BRANCH']}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        return None

    js = r.json()
    if "content" not in js:
        return None

    try:
        return base64.b64decode(js["content"])
    except:
        return None


# -----------------------------------------------------------
# SQLite Loader
# -----------------------------------------------------------
def load_sqlite_bytes(db_bytes):
    if not db_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        if not tables:
            conn.close()
            return pd.DataFrame()
        table = tables[0]
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

    except:
        return pd.DataFrame()

    # Rename columns
    rename = {}
    for col in df.columns:
        c = col.lower()
        if c == "id":
            rename[col] = "id"
        elif "work" in c:
            rename[col] = "work_order"
        elif "shift" in c:
            rename[col] = "shift"
        elif "device" in c:
            rename[col] = "device"
        elif "timestamp" in c:
            rename[col] = "timestamp"
        elif "time" in c:
            rename[col] = "time_str"
        elif "temp" in c:
            rename[col] = "temperature"
        elif "curr" in c:
            rename[col] = "current"

    df = df.rename(columns=rename)

    # Fill missing columns
    needed = ["id", "work_order", "shift", "device", "timestamp", "time_str", "temperature", "current"]
    for col in needed:
        if col not in df.columns:
            df[col] = None

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    return df.sort_values("ts_dt")


# -----------------------------------------------------------
# 📡 Realtime Page (Zoomable + No Duplicate Keys)
# -----------------------------------------------------------
def realtime_page():

    st.header("📡 即時趨勢圖（Zoomable，5 秒自動更新）")

    # container only refreshes content inside (no duplicate keys)
    plot_container = st.container()

    # Start loop
    while True:

        # ⛔ If user switches page → stop loop
        if st.session_state.get("current_page") != "實時資料":
            break

        db_bytes = gh_download_file("Data/local/local_realtime.db")
        df = load_sqlite_bytes(db_bytes)

        with plot_container:
            st.subheader("裝置圖表 (Auto-refresh)")

            if df.empty:
                st.warning("尚無資料")
                time.sleep(5)
                continue

            df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
            df["current"] = pd.to_numeric(df["current"], errors="coerce")
            df = df.dropna(subset=["ts_dt"])

            devices = sorted(df["device"].dropna().unique())

            for dev in devices:
                dev_df = df[df["device"] == dev].sort_values("ts_dt")
                if dev_df.empty:
                    continue

                st.markdown(f"### 📟 裝置：**{dev}**")

                # ---------------------------------------------------------
                # Temperature plot
                # ---------------------------------------------------------
                fig_temp = go.Figure()
                fig_temp.add_trace(go.Scatter(
                    x=dev_df["ts_dt"],
                    y=dev_df["temperature"],
                    mode="lines",
                    line=dict(color="red", width=2),
                ))
                fig_temp.update_layout(
                    title="Temperature (°C)",
                    height=280,
                    xaxis_title="Time",
                    yaxis_title="°C",
                    margin=dict(l=10, r=10, t=40, b=10)
                )

                st.plotly_chart(fig_temp, width="stretch", key=f"temp_{dev}_{time.time()}")

                # ---------------------------------------------------------
                # Current plot
                # ---------------------------------------------------------
                fig_curr = go.Figure()
                fig_curr.add_trace(go.Scatter(
                    x=dev_df["ts_dt"],
                    y=dev_df["current"],
                    mode="lines",
                    line=dict(color="blue", width=2),
                ))
                fig_curr.update_layout(
                    title="Current (A)",
                    height=280,
                    xaxis_title="Time",
                    yaxis_title="A",
                    margin=dict(l=10, r=10, t=40, b=10)
                )

                st.plotly_chart(fig_curr, width="stretch", key=f"curr_{dev}_{time.time()}")

        # Refresh every 5 seconds
        time.sleep(5)


# -----------------------------------------------------------
# Dummy History Page
# -----------------------------------------------------------
def history_page():
    st.header("📚 歷史資料")
    st.info("History page placeholder.")


# -----------------------------------------------------------
# Navigation
# -----------------------------------------------------------
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
st.session_state["current_page"] = page

if page == "實時資料":
    realtime_page()
else:
    history_page()
