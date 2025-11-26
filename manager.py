import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime

# ============================================================
# Streamlit 設定
# ============================================================
st.set_page_config(page_title="EMS 管理台", layout="wide")
st.title("EMS 管理台")

# ============================================================
# 讀取 GitHub DB（固定 EMS、避免讀 recording）
# ============================================================
GIT_OWNER  = st.secrets["GIT_OWNER"]
GIT_REPO   = st.secrets["GIT_REPO"]
GIT_BRANCH = st.secrets["GIT_BRANCH"]
GIT_TOKEN  = st.secrets["GIT_TOKEN"]

def gh_headers():
    return {
        "Authorization": f"Bearer {GIT_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

def gh_download_file(path):
    """下載 GitHub 上 EMS 的 DB。"""
    url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        return None

    try:
        return base64.b64decode(r.json()["content"])
    except:
        return None

# ============================================================
# SQLite 通用讀取（標準化欄位）
# ============================================================
def load_sqlite_bytes(db_bytes):
    if not db_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(tmp)
        df = pd.read_sql_query("SELECT * FROM records", conn)
        conn.close()
    except:
        return pd.DataFrame()

    mapping = {}
    for c in df.columns:
        lc = c.lower()
        mapping[c] = (
            "id" if lc == "id" else
            "work_order" if "work" in lc else
            "shift" if "shift" in lc else
            "device" if "device" in lc else
            "timestamp" if "timestamp" in lc else
            "time_str" if "time" in lc else
            "temperature" if "temp" in lc else
            "current" if "curr" in lc else c
        )
    df = df.rename(columns=mapping)

    # 補齊欄位
    for col in ["id", "work_order", "shift", "device", "timestamp",
                "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    return df.sort_values("ts_dt")

# ============================================================
# 📡 實時資料（每 5 秒局部更新 + 圖表）
# ============================================================
def realtime_page():

    st.header("📡 即時資料（每 5 秒自動更新）")

    # 初始化 timer
    if "rt_last_refresh" not in st.session_state:
        st.session_state["rt_last_refresh"] = datetime.now()

    # 計算是否距離上次 5 秒
    now = datetime.now()
    diff = (now - st.session_state["rt_last_refresh"]).total_seconds()

    # 只更新圖表，不刷新整頁
    if diff >= 5:
        st.session_state["rt_last_refresh"] = now
        st.rerun()    # 🔥 局部 rerun 只刷新本頁，不跳轉、不跳回頂端

    # --- 讀取資料 ---
    db_bytes = gh_download_file("Data/local/local_realtime.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無即時資料")
        return

    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    st.subheader("📈 Temperature")
    st.line_chart(df.set_index("ts_dt")["temperature"], height=260, width="stretch")

    st.subheader("📉 Current")
    st.line_chart(df.set_index("ts_dt")["current"], height=260, width="stretch")

# ============================================================
# 📚 歷史資料頁面（完整）
# ============================================================
def history_page():

    st.header("📚 歷史資料")

    db_bytes = gh_download_file("Data/local/local_historical.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無歷史資料")
        return

    df["date"] = df["time_str"].str[:10]

    sel_date = st.selectbox("選擇日期", sorted(df["date"].dropna().unique()))
    df = df[df["date"] == sel_date]

    orders = sorted(df["work_order"].dropna().unique())
    sel_order = st.selectbox("工單", ["全部"] + orders)
    if sel_order != "全部":
        df = df[df["work_order"] == sel_order]

    devices = sorted(df["device"].dropna().unique())
    sel_dev = st.selectbox("裝置（device）", ["全部"] + devices)
    if sel_dev != "全部":
        df = df[df["device"] == sel_dev]

    st.subheader("📃 篩選後資料")
    st.dataframe(df, width="stretch")

    # --- 趨勢圖 ---
    st.subheader("📈 歷史趨勢圖")

    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    for dev in sorted(df["device"].dropna().unique()):
        dev_df = df[df["device"] == dev]

        st.markdown(f"### 🟦 Device: {dev}")

        c1, c2 = st.columns(2)

        with c1:
            st.write("Temperature")
            st.line_chart(dev_df.set_index("ts_dt")["temperature"], height=250, width="stretch")

        with c2:
            st.write("Current")
            st.line_chart(dev_df.set_index("ts_dt")["current"], height=250, width="stretch")

# ============================================================
# Main
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
st.session_state["current_page"] = page

if page == "實時資料":
    realtime_page()
else:
    history_page()
