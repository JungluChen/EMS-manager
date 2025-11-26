import streamlit as st
import pandas as pd
import sqlite3
import json
import requests
import base64
import tempfile
from pathlib import Path
from datetime import datetime

try:
    from streamlit_autorefresh import st_autorefresh
except:
    st_autorefresh = None

# ============================================================
# Streamlit Config
# ============================================================
st.set_page_config(page_title="EMS 管理台", layout="wide")

# ============================================================
# GitHub API (強制只讀 EMS repo)
# ============================================================

def gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['GIT_TOKEN']}",
        "Accept": "application/vnd.github+json"
    }

def gh_owner_repo_branch():
    return (
        st.secrets["GIT_OWNER"],
        st.secrets["GIT_REPO"],
        st.secrets.get("GIT_BRANCH", "main"),
    )

def gh_download_file(path):
    """只讀 EMS repo，不再讀 ALT_REPOS"""
    owner, repo, branch = gh_owner_repo_branch()

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=gh_headers(), timeout=20)

    if r.status_code != 200:
        st.error(f"下載失敗：HTTP {r.status_code} ({path})")
        return None

    content = r.json().get("content")
    if not content:
        st.error("GitHub 回傳空內容")
        return None

    return base64.b64decode(content)


# ============================================================
# SQLite Loader + Debug Tools
# ============================================================

def debug_show_db(db_bytes):
    """顯示 DB 基本資訊，方便抓問題"""
    if not db_bytes:
        st.write("❌ DB is None or empty")
        return

    st.write("📦 Downloaded DB size =", len(db_bytes), "bytes")

    tmp = Path(tempfile.gettempdir()) / "debug.sqlite"
    tmp.write_bytes(db_bytes)

    conn = sqlite3.connect(tmp)
    cur = conn.cursor()

    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    st.write("📋 Tables:", tables)

    try:
        rows = cur.execute("SELECT * FROM records LIMIT 5").fetchall()
        st.write("🔍 Sample rows:", rows)
    except:
        st.write("⚠ Table 'records' not found")

    conn.close()


# ============================================================
# History DB Loader (強制解析你的格式)
# ============================================================

def load_history_db(date_filter=None):

    db = gh_download_file("Data/local/local_historical.db")
    if not db:
        return pd.DataFrame()

    # Debug output
    with st.expander("🛠 Debug DB 內容 (可收起)"):
        debug_show_db(db)

    tmp = Path(tempfile.gettempdir()) / "tmp_history.sqlite"
    tmp.write_bytes(db)

    try:
        conn = sqlite3.connect(tmp)
        df = pd.read_sql_query("SELECT * FROM records", conn)
        conn.close()
    except Exception as e:
        st.error(f"讀取資料失敗：{e}")
        return pd.DataFrame()

    # 補齊欄位（防止不同版本 DB）
    for col in ["id", "work_order", "shift", "device",
                "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None

    # 你的時間格式： 2025/11/26 13:13:28
    df["ts_dt"] = pd.to_datetime(
        df["time_str"],
        format="%Y/%m/%d %H:%M:%S",
        errors="coerce"
    )

    if date_filter:
        df = df[df["time_str"].str.startswith(date_filter)]

    return df.sort_values("ts_dt")


# ============================================================
# Real-time Loader（保持不變）
# ============================================================

def load_realtime_db():
    db = gh_download_file("Data/local/local_realtime.db")
    if not db:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "rt.sqlite"
    tmp.write_bytes(db)

    try:
        conn = sqlite3.connect(tmp)
        df = pd.read_sql_query(
            """SELECT * FROM records ORDER BY id DESC LIMIT 1000""",
            conn
        )
        conn.close()
    except:
        return pd.DataFrame()

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    return df.sort_values("ts_dt")


# ============================================================
# Utility
# ============================================================

def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0

def _compute_device_runtime(df):
    runtimes = {}
    if df.empty:
        return runtimes
    for dev in df["device"].dropna().unique():
        sub = df[df["device"] == dev]["ts_dt"].dropna()
        if sub.empty: continue
        runtimes[dev] = (sub.max() - sub.min()).total_seconds()
    return runtimes


# ============================================================
# UI: Real-time Data
# ============================================================

def realtime_page():
    st.header("实时数据")
    if st_autorefresh:
        st_autorefresh(interval=5000, key="rt-refresh")

    df = load_realtime_db()
    if df.empty:
        st.info("尚无实时数据")
        return

    latest = df.groupby("device").tail(1)
    devices = sorted(latest["device"].unique())
    runtimes = _compute_device_runtime(df)

    cols = st.columns(len(devices))
    for i, dev in enumerate(devices):
        row = latest[latest["device"] == dev].iloc[0]
        with cols[i]:
            st.metric(f"{dev} Temperature", f"{safe_float(row.temperature):.1f}")
            st.metric(f"{dev} Current", f"{safe_float(row.current):.2f}")
            st.metric(f"{dev} Runtime", f"{int(runtimes.get(dev, 0))} sec")

    st.subheader("設備快照")
    st.dataframe(latest)

    st.subheader("趨勢圖")
    for dev in devices:
        sub = df[df["device"] == dev]
        st.line_chart(sub.set_index("ts_dt")[["temperature", "current"]])


# ============================================================
# UI: History Data
# ============================================================

def history_page():
    st.header("历史数据")

    df_all = load_history_db()
    if df_all.empty:
        st.info("尚无历史数据")
        return

    dates = sorted(df_all["time_str"].str[:10].unique())
    sel_date = st.selectbox("選擇日期", dates)

    df = load_history_db(sel_date)

    st.subheader("資料表")
    st.dataframe(df)

    runtimes = _compute_device_runtime(df)
    st.subheader("設備快照")
    st.write(runtimes)

    st.subheader("趨勢圖")
    for dev in sorted(df["device"].dropna().unique()):
        sub = df[df["device"] == dev]
        st.line_chart(sub.set_index("ts_dt")[["temperature", "current"]])


# ============================================================
# Main
# ============================================================

def main():
    st.title("EMS 管理台")

    page = st.sidebar.radio("頁面", ["实时数据", "历史数据"])

    if page == "实时数据":
        realtime_page()
    else:
        history_page()


if __name__ == "__main__":
    main()
