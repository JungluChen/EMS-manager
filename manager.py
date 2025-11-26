import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime
import altair as alt

# ============================================================
# Streamlit Config
# ============================================================
st.set_page_config(page_title="EMS 管理台", layout="wide")
st.title("EMS 管理台")

# ============================================================
# GitHub 設定（固定 EMS repo）
# ============================================================
GIT_OWNER = st.secrets["GIT_OWNER"]
GIT_REPO = st.secrets["GIT_REPO"]
GIT_BRANCH = st.secrets["GIT_BRANCH"]
GIT_TOKEN = st.secrets["GIT_TOKEN"]

def gh_headers():
    return {
        "Authorization": f"Bearer {GIT_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

def gh_download_file(path):
    url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        st.error(f"❌ GitHub 下載失敗：HTTP {r.status_code} → {path}")
        return None

    js = r.json()
    if "content" not in js:
        st.error(f"❌ GitHub 回傳格式錯誤（缺少 content）")
        return None

    try:
        return base64.b64decode(js["content"])
    except Exception:
        st.error("❌ Base64 解碼失敗")
        return None


# ============================================================
# SQLite 載入（自動欄位 mapping）
# ============================================================
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
            return pd.DataFrame()
        table = tables[0]

        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

    except Exception as e:
        st.error(f"SQLite 讀取失敗：{e}")
        return pd.DataFrame()

    # 欄位 mapping
    rename_map = {}
    for c in df.columns:
        lc = c.lower()
        if lc == "id":
            rename_map[c] = "id"
        elif "work" in lc:
            rename_map[c] = "work_order"
        elif "shift" in lc:
            rename_map[c] = "shift"
        elif "device" in lc:
            rename_map[c] = "device"
        elif "timestamp" in lc:
            rename_map[c] = "timestamp"
        elif "time" in lc:
            rename_map[c] = "time_str"
        elif "temp" in lc:
            rename_map[c] = "temperature"
        elif "curr" in lc:
            rename_map[c] = "current"

    df = df.rename(columns=rename_map)

    # 補欄位
    for col in ["id", "work_order", "shift", "device",
                "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    return df.sort_values("ts_dt")


# ============================================================
# Real-time Page
# ============================================================
def chart_device(dev_df):
    """合併溫度 + 電流的折線圖（共用 X 軸，Y 軸 0–100）"""

    chart = alt.Chart(dev_df).transform_fold(
        ["temperature", "current"],
        as_=["type", "value"]
    ).mark_line().encode(
        x=alt.X("ts_dt:T", title="時間"),
        y=alt.Y("value:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("type:N", title="參數"),
        tooltip=[
            alt.Tooltip("ts_dt:T", title="時間"),
            alt.Tooltip("type:N", title="種類"),
            alt.Tooltip("value:Q", title="數值")
        ]
    ).properties(height=200)

    return chart


def realtime_page():
    st.header("📡 即時資料（每 5 秒更新）")

    # 5 秒局部刷新
    if "rt_last_refresh" not in st.session_state:
        st.session_state["rt_last_refresh"] = datetime.now()

    now = datetime.now()
    if (now - st.session_state["rt_last_refresh"]).total_seconds() >= 5:
        st.session_state["rt_last_refresh"] = now
        st.rerun()

    # --- Load DB ---
    db_bytes = gh_download_file("Data/local/local_realtime.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無即時資料")
        return

    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    devices = sorted(df["device"].dropna().unique())

    N_PER_ROW = 2

    for i in range(0, len(devices), N_PER_ROW):
        row_devices = devices[i:i + N_PER_ROW]
        cols = st.columns(len(row_devices))

        for idx, dev in enumerate(row_devices):
            dev_df = df[df["device"] == dev].sort_values("ts_dt")

            if dev_df.empty:
                continue

            last_time = dev_df["ts_dt"].max()
            delay = (now - last_time).total_seconds()

            if delay < 10:
                status = "🟢 Online"
            elif delay < 30:
                status = "🟠 Delayed"
            else:
                status = "🔴 Offline"

            with cols[idx]:
                st.markdown(f"### 🖥️ {dev}")
                st.caption(f"Last update: **{last_time}**")
                st.caption(f"Status: {status}")

                st.altair_chart(chart_device(dev_df), use_container_width=True)


# ============================================================
# History Page
# ============================================================
def history_page():
    st.header("📚 歷史資料")

    db_bytes = gh_download_file("Data/local/local_historical.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無歷史資料")
        return

    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    df["date"] = df["time_str"].str[:10]
    date_list = sorted(df["date"].dropna().unique())

    sel_date = st.selectbox("選擇日期", date_list)
    df = df[df["date"] == sel_date]

    orders = sorted(df["work_order"].dropna().unique())
    sel_order = st.selectbox("工單", ["全部"] + orders)

    if sel_order != "全部":
        df = df[df["work_order"] == sel_order]

    devices = sorted(df["device"].dropna().unique())
    sel_dev = st.selectbox("機器", ["全部"] + devices)

    if sel_dev != "全部":
        df = df[df["device"] == sel_dev]

    st.subheader("📄 資料表")
    st.dataframe(df, width="stretch")

    st.subheader("📈 趨勢圖")

    for dev in sorted(df["device"].dropna().unique()):
        dev_df = df[df["device"] == dev].sort_values("ts_dt")
        if dev_df.empty:
            continue

        st.markdown(f"### 🖥️ {dev}")
        st.altair_chart(chart_device(dev_df), use_container_width=True)


# ============================================================
# Main
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
if page == "實時資料":
    realtime_page()
else:
    history_page()
