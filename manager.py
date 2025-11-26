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
        st.error(f"下載失敗：HTTP {r.status_code} → {path}")
        return None

    js = r.json()
    if "content" not in js:
        st.error(f"GitHub content missing → {path}")
        return None

    try:
        return base64.b64decode(js["content"])
    except:
        st.error(f"Base64 decode error → {path}")
        return None


# -----------------------------------------------------------
# SQLite Loader
# -----------------------------------------------------------
def load_sqlite_bytes(db_bytes):
    """讀取 SQLite Byte → DataFrame"""
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

        df = pd.read_sql_query(f"SELECT * FROM {tables[0]}", conn)
        conn.close()

    except Exception as e:
        st.error(f"SQLite read error: {e}")
        return pd.DataFrame()

    # 欄位 mapping
    rename_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc == "id":
            rename_map[col] = "id"
        elif "work" in lc:
            rename_map[col] = "work_order"
        elif "shift" in lc:
            rename_map[col] = "shift"
        elif "device" in lc:
            rename_map[col] = "device"
        elif "timestamp" in lc:
            rename_map[col] = "timestamp"
        elif "time_str" in lc or "timestr" in lc or "time" in lc:
            rename_map[col] = "time_str"
        elif "temp" in lc:
            rename_map[col] = "temperature"
        elif "curr" in lc:
            rename_map[col] = "current"

    df = df.rename(columns=rename_map)

    # 補齊欄位
    for col in ["id", "work_order", "shift", "device", "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    # 時間轉換
    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    return df.sort_values("ts_dt")


# -----------------------------------------------------------
# 📡 Real-time Zoomable Trend Page
# -----------------------------------------------------------
def realtime_page():

    st.header("📡 即時趨勢圖（Zoomable，5 秒自動更新）")

    chart_area = st.empty()  # only refresh this block

    while True:
        # 如果切換頁面 → 停止更新
        if st.session_state.get("current_page") != "實時資料":
            break

        db_bytes = gh_download_file("Data/local/local_realtime.db")
        df = load_sqlite_bytes(db_bytes)

        with chart_area:
            if df.empty:
                st.warning("尚無即時資料")
                time.sleep(5)
                continue

            # 數值轉換
            df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
            df["current"] = pd.to_numeric(df["current"], errors="coerce")
            df = df.dropna(subset=["ts_dt"])

            devices = sorted(df["device"].dropna().unique())

            for dev in devices:
                dev_df = df[df["device"] == dev].sort_values("ts_dt")
                if dev_df.empty:
                    continue

                st.subheader(f"📟 裝置：{dev}")

                # ---------------------------------------------------------
                # Temperature Plot (Zoomable)
                # ---------------------------------------------------------
                fig_temp = go.Figure()
                fig_temp.add_trace(go.Scatter(
                    x=dev_df["ts_dt"],
                    y=dev_df["temperature"],
                    mode="lines",
                    line=dict(color="red", width=2),
                    name="Temperature"
                ))
                fig_temp.update_layout(
                    title="Temperature (°C)",
                    height=300,
                    xaxis_title="Time",
                    yaxis_title="Temperature",
                    margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_temp, use_container_width=True, key=f"temp_{dev}")

                # ---------------------------------------------------------
                # Current Plot (Zoomable)
                # ---------------------------------------------------------
                fig_curr = go.Figure()
                fig_curr.add_trace(go.Scatter(
                    x=dev_df["ts_dt"],
                    y=dev_df["current"],
                    mode="lines",
                    line=dict(color="blue", width=2),
                    name="Current"
                ))
                fig_curr.update_layout(
                    title="Current (A)",
                    height=300,
                    xaxis_title="Time",
                    yaxis_title="Current",
                    margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_curr, use_container_width=True, key=f"curr_{dev}")

        time.sleep(5)  # update every 5 seconds


# -----------------------------------------------------------
# 📚 歷史資料頁面（原本版本即可）
# -----------------------------------------------------------
def history_page():
    st.header("📚 歷史資料")
    st.info("（你可放你的歷史頁面程式碼）")


# -----------------------------------------------------------
# Main Navigation
# -----------------------------------------------------------
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
st.session_state["current_page"] = page

if page == "實時資料":
    realtime_page()
else:
    history_page()
