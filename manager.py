import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime
import time
import plotly.express as px

# ============================================================
# Streamlit Config
# ============================================================
st.set_page_config(page_title="EMS 管理台", layout="wide")
st.title("EMS 管理台")

# ============================================================
# GitHub 設定（固定 EMS repo）
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
    """下載 GitHub 上的二進位檔案"""
    url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
    r = requests.get(url, headers=gh_headers(), timeout=20)
    if r.status_code != 200:
        st.error(f"下載失敗：HTTP {r.status_code} → {path}")
        return None
    js = r.json()
    if "content" not in js:
        return None
    return base64.b64decode(js["content"])

# ============================================================
# SQLite Data Loader
# ============================================================
def load_sqlite_bytes(db_bytes):
    if not db_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()

        # 找資料表
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        if not tables:
            return pd.DataFrame()

        table = tables[0]  # 你的 db 都只有 1 張表

        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

    except Exception as e:
        st.error(f"SQLite 讀取失敗：{e}")
        return pd.DataFrame()

    # 欄位 mapping
    rename_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc == "id": rename_map[col] = "id"
        elif "work" in lc: rename_map[col] = "work_order"
        elif "shift" in lc: rename_map[col] = "shift"
        elif "device" in lc: rename_map[col] = "device"
        elif "timestamp" in lc: rename_map[col] = "timestamp"
        elif "time" in lc: rename_map[col] = "time_str"
        elif "temp" in lc: rename_map[col] = "temperature"
        elif "curr" in lc: rename_map[col] = "current"

    df = df.rename(columns=rename_map)

    # 補缺欄位
    for c in ["id","work_order","shift","device","timestamp","time_str","temperature","current"]:
        if c not in df.columns:
            df[c] = None

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    return df.sort_values("ts_dt")

# ============================================================
# 📡 即時資料（圖表 + 固定顏色 + 局部刷新）
# ============================================================
def realtime_page():
    st.header("📡 即時趨勢圖（每 5 秒更新，不刷新整頁）")

    # 只刷新以下區塊
    chart_area = st.empty()

    while True:

        db_bytes = gh_download_file("Data/local/local_realtime.db")
        df = load_sqlite_bytes(db_bytes)

        # 如果離開頁面 → 停止 while loop
        if st.session_state.get("current_page") != "實時資料":
            break

        if df.empty:
            chart_area.warning("尚無即時資料")
            time.sleep(5)
            continue

        # 轉型態
        df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
        df["current"] = pd.to_numeric(df["current"], errors="coerce")

        devices = sorted(df["device"].dropna().unique().tolist())

        with chart_area:
            for dev in devices:
                dev_df = df[df["device"] == dev]

                if dev_df.empty:
                    continue

                st.subheader(f"裝置：{dev}")

                # --- Fixed color chart ---
                fig_temp = px.line(
                    dev_df,
                    x="ts_dt", y="temperature",
                    title="Temperature",
                    markers=False
                )
                fig_temp.update_traces(line=dict(color="#FF4B4B", width=2))
                st.plotly_chart(fig_temp, use_container_width=True)

                fig_curr = px.line(
                    dev_df,
                    x="ts_dt", y="current",
                    title="Current",
                    markers=False
                )
                fig_curr.update_traces(line=dict(color="#4B7BFF", width=2))
                st.plotly_chart(fig_curr, use_container_width=True)

        time.sleep(5)

# ============================================================
# 📚 歷史資料（完整篩選 + 趨勢圖）
# ============================================================
def history_page():
    st.header("📚 歷史資料")

    db_bytes = gh_download_file("Data/local/local_historical.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無歷史資料")
        return

    st.success(f"成功載入 {len(df)} 筆資料")

    # 日期
    df["date"] = df["time_str"].str[:10]
    dates = sorted(df["date"].dropna().unique())

    sel_date = st.selectbox("選擇日期", dates)
    df = df[df["date"] == sel_date]

    # 工單
    orders = sorted(df["work_order"].dropna().unique())
    sel_order = st.selectbox("選擇工單", ["全部"] + orders)
    if sel_order != "全部":
        df = df[df["work_order"] == sel_order]

    # 裝置
    devices = sorted(df["device"].dropna().unique())
    sel_dev = st.selectbox("選擇設備", ["全部"] + devices)
    if sel_dev != "全部":
        df = df[df["device"] == sel_dev]

    st.subheader("篩選後資料")
    st.dataframe(df, use_container_width=True)

    # 趨勢圖
    st.subheader("📈 歷史趨勢圖")

    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    for dev in sorted(df["device"].dropna().unique()):
        dev_df = df[df["device"] == dev]

        if dev_df.empty:
            continue

        st.markdown(f"### 🟦 Device：{dev}")

        fig_temp = px.line(
            dev_df, x="ts_dt", y="temperature", title="Temperature"
        )
        fig_temp.update_traces(line=dict(color="#FF4B4B"))
        st.plotly_chart(fig_temp, use_container_width=True)

        fig_curr = px.line(
            dev_df, x="ts_dt", y="current", title="Current"
        )
        fig_curr.update_traces(line=dict(color="#4B7BFF"))
        st.plotly_chart(fig_curr, use_container_width=True)

# ============================================================
# Main
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
st.session_state["current_page"] = page

if page == "實時資料":
    realtime_page()
else:
    history_page()
