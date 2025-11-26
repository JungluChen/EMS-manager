import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime

st.set_page_config(page_title="EMS 管理台", layout="wide")
st.title("EMS 管理台")

# ============================================================
# GitHub 設定（固定 EMS repo，不會讀錯）
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
    """固定讀 EMS，避免讀 recording。"""
    url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        st.error(f"下載失敗：HTTP {r.status_code} → {path}")
        return None

    js = r.json()
    if "content" not in js:
        st.error(f"GitHub 回傳異常（缺少 content）：{path}")
        return None

    try:
        return base64.b64decode(js["content"])
    except:
        st.error(f"Base64 解碼失敗：{path}")
        return None

# ============================================================
# SQLite 自動解析（realtime / historical 共用）
# ============================================================
def load_sqlite_bytes(db_bytes):
    if not db_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()

        # 找第一個 table（你的 DB 就一張）
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [x[0] for x in cur.fetchall()]
        if not tables:
            conn.close()
            return pd.DataFrame()
        table = tables[0]

        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

    except Exception as e:
        st.error(f"SQLite 讀取失敗：{e}")
        return pd.DataFrame()

    # ---- 欄位標準化 ----
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

    # 補齊缺少欄位
    for col in ["id", "work_order", "shift", "device", "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    return df.sort_values("ts_dt")

# ============================================================
# 📡 實時資料頁面
# ============================================================
def realtime_page():
    st.header("📡 即時資料")

    db_bytes = gh_download_file("Data/local/local_realtime.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無即時資料")
        return

    st.dataframe(df, use_container_width=True)

# ============================================================
# 📚 歷史資料頁面（包含完整趨勢圖 + 工單 / 機器篩選）
# ============================================================
def history_page():
    st.header("📚 歷史資料")

    db_bytes = gh_download_file("Data/local/local_historical.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無歷史資料")
        return

    st.success(f"成功載入 {len(df)} 筆資料")

    # -------------------------
    # 🔎 日期篩選
    # -------------------------
    df["date"] = df["time_str"].str[:10]
    date_list = sorted(df["date"].dropna().unique())

    sel_date = st.selectbox("選擇日期", date_list)
    df = df[df["date"] == sel_date]

    # -------------------------
    # 🔎 工單篩選
    # -------------------------
    orders = sorted(df["work_order"].dropna().unique())
    sel_order = st.selectbox("選擇工單（work_order）", ["全部"] + orders)

    if sel_order != "全部":
        df = df[df["work_order"] == sel_order]

    # -------------------------
    # 🔎 機器篩選
    # -------------------------
    devices = sorted(df["device"].dropna().unique())
    sel_dev = st.selectbox("選擇機器（device）", ["全部"] + devices)

    if sel_dev != "全部":
        df = df[df["device"] == sel_dev]

    st.subheader("📄 篩選後資料表")
    st.dataframe(df, use_container_width=True)

    # ============================================================
    # 📈 完整歷史趨勢圖（依裝置分開）
    # ============================================================
    st.subheader("📈 趨勢圖（歷史曲線）")

    if df.empty:
        st.info("沒有符合條件的資料")
        return

    # 氣溫 / 電流 轉 float
    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")

    device_list = sorted(df["device"].dropna().unique())

    for dev in device_list:
        dev_df = df[df["device"] == dev]

        if dev_df.empty:
            continue

        st.markdown(f"### 🟦 Device：**{dev}**")

        c1, c2 = st.columns(2)

        with c1:
            st.line_chart(
                dev_df.set_index("ts_dt")["temperature"],
                height=250
            )

        with c2:
            st.line_chart(
                dev_df.set_index("ts_dt")["current"],
                height=250
            )

# ============================================================
# Main
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])

if page == "實時資料":
    realtime_page()
else:
    history_page()
