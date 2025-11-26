import streamlit as st
import pandas as pd
import sqlite3
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime

# ============================================================
# Streamlit Config
# ============================================================
st.set_page_config(page_title="EMS 管理台", layout="wide")
st.title("EMS 管理台")

# ============================================================
# GitHub Fixed Config（強制 EMS-only）
# ============================================================
GIT_OWNER  = st.secrets["GIT_OWNER"]
GIT_REPO   = st.secrets["GIT_REPO"]     # 必須是 EMS
GIT_BRANCH = st.secrets["GIT_BRANCH"]
GIT_TOKEN  = st.secrets["GIT_TOKEN"]

# ---- Firebase headers ----
def gh_headers():
    return {
        "Authorization": f"Bearer {GIT_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

# ============================================================
# GitHub Load DB File (fixed single repo)
# ============================================================
def gh_download_file(path):
    """
    強制讀取 EMS/ path
    """
    url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        st.error(f"下載失敗：HTTP {r.status_code} ({path})")
        return None

    js = r.json()
    if "content" not in js:
        st.error(f"GitHub 回傳無 content 欄位：{path}")
        return None

    try:
        return base64.b64decode(js["content"])
    except:
        st.error(f"Base64 解碼失敗：{path}")
        return None

# ============================================================
# SQLite 讀取（適用 realtime/historical）
# ============================================================
def load_sqlite_bytes(db_bytes):
    """
    完整自動偵測 table / 欄位 mapping
    """
    if not db_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()

        # 找 table
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
        if lc in ("id",):
            rename_map[c] = "id"
        elif "work" in lc:
            rename_map[c] = "work_order"
        elif "shift" in lc:
            rename_map[c] = "shift"
        elif "device" in lc:
            rename_map[c] = "device"
        elif "timestamp" in lc:
            rename_map[c] = "timestamp"
        elif "time_str" in lc or "time" in lc:
            rename_map[c] = "time_str"
        elif "temp" in lc:
            rename_map[c] = "temperature"
            continue
        elif "curr" in lc:
            rename_map[c] = "current"
            continue

    df = df.rename(columns=rename_map)

    # ---- 補齊欄位 ----
    for col in ["id", "work_order", "shift", "device", "timestamp",
                "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    # ---- 時間格式 ----
    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    return df.sort_values("ts_dt")


# ============================================================
# UI: Realtime Page
# ============================================================
def realtime_page():
    st.header("📡 實時資料")

    db_bytes = gh_download_file("Data/local/local_realtime.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無實時資料")
        return

    st.dataframe(df, use_container_width=True)

# ============================================================
# UI: History Page
# ============================================================
def history_page():
    st.header("📚 歷史資料")

    db_bytes = gh_download_file("Data/local/local_historical.db")
    df = load_sqlite_bytes(db_bytes)

    if df.empty:
        st.info("尚無歷史資料")
        return

    st.success(f"成功載入 {len(df)} 筆資料")

    # 日期選擇
    if "time_str" in df.columns:
        df["date"] = df["time_str"].str[:10]
        dates = sorted(df["date"].dropna().unique())
        sel_date = st.selectbox("選擇日期", dates)

        df = df[df["date"] == sel_date]

    st.dataframe(df, use_container_width=True)


# ============================================================
# MAIN
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])

if page == "實時資料":
    realtime_page()
else:
    history_page()
