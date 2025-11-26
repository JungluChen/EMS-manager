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
# GitHub API Functions
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

def gh_repos():
    primary = st.secrets.get("GIT_REPO", "")
    alts = st.secrets.get("ALT_REPOS", "EMS,recording")
    names = [x.strip() for x in (primary + "," + alts).split(",") if x.strip()]
    seen = set(); out = []
    for n in names:
        if n not in seen:
            out.append(n); seen.add(n)
    return out

def gh_download_file(path):
    owner, _, branch = gh_owner_repo_branch()
    for repo in gh_repos():
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code != 200:
            continue
        content = r.json().get("content", None)
        if not content:
            continue
        try:
            return base64.b64decode(content)
        except:
            continue
    return None

def gh_upload_file(path, content_bytes, message="update file"):
    owner, repo, branch = gh_owner_repo_branch()

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    b64 = base64.b64encode(content_bytes).decode("utf-8")

    payload = {"message": message, "content": b64, "branch": branch}
    if sha:
        payload["sha"] = sha

    put_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    pr = requests.put(put_url, headers=gh_headers(), json=payload)
    return pr.status_code in (200, 201)


# ============================================================
# Helpers
# ============================================================

def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except:
        return default

def _format_duration(sec):
    try:
        sec = int(max(0, float(sec)))
    except:
        sec = 0
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"

def _compute_device_runtime(df):
    runtimes = {}
    if df.empty:
        return runtimes

    for dev in df["device"].dropna().unique().tolist():
        sub = df[df["device"] == dev]["ts_dt"].dropna()
        if sub.empty:
            continue
        runtimes[dev] = (sub.max() - sub.min()).total_seconds()
    return runtimes


# ============================================================
# SQLite Loading - New Schema
# ============================================================

def load_sqlite_from_bytes(db_bytes, date_filter=None):
    tmp = Path(tempfile.gettempdir()) / "tmp_history.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(str(tmp))
        cur = conn.cursor()

        if date_filter:
            cur.execute(
                """SELECT id, work_order, shift, device, timestamp, time_str,
                          temperature, current
                   FROM records
                   WHERE time_str LIKE ?
                   ORDER BY id ASC""",
                (date_filter + "%",),
            )
        else:
            cur.execute(
                """SELECT id, work_order, shift, device, timestamp, time_str,
                          temperature, current
                   FROM records
                   ORDER BY id ASC"""
            )

        rows = cur.fetchall()
        conn.close()

    except:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "id", "work_order", "shift", "device",
        "timestamp", "time_str", "temperature", "current"
    ])

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    return df


def load_realtime_db():
    db = gh_download_file("Data/local/local_realtime.db")
    if not db:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "realtime.sqlite"
    tmp.write_bytes(db)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, work_order, shift, device, timestamp, time_str,
                      temperature, current
               FROM records
               ORDER BY id DESC
               LIMIT 1000"""
        )
        rows = cur.fetchall()
        conn.close()
    except:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "id", "work_order", "shift", "device",
        "timestamp", "time_str", "temperature", "current"
    ])

    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    return df.sort_values("ts_dt")


def load_history_db(date_filter=None):
    """
    更強健的 history.db 讀取器：
    - 自動偵測表名
    - 自動偵測欄位
    - 自動 mapping 成標準欄位
    Data/local/local_historical.db
    Data/local/local_historical.db
    """
    db = gh_download_file("Data/local/local_realtime.db")
    if not db:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "tmp_history.sqlite"
    tmp.write_bytes(db)

    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()

        # 自動找出第一個資料表
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_list = [row[0] for row in cur.fetchall()]
        if not table_list:
            conn.close()
            return pd.DataFrame()

        table = table_list[0]  # 使用第一個表

        # 讀取全部資料
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [row[1] for row in cur.fetchall()]

        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

    except Exception as e:
        st.error(f"讀取歷史資料庫失敗：{e}")
        return pd.DataFrame()

    # ---- 欄位 mapping 成統一格式 ----
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
        elif "time_str" in lc or "timestr" in lc or "time" in lc:
            rename_map[c] = "time_str"
        elif "temp" in lc:
            rename_map[c] = "temperature"
        elif "curr" in lc:
            rename_map[c] = "current"

    df = df.rename(columns=rename_map)

    # ---- 只保留標準欄位 ----
    for col in ["id", "work_order", "shift", "device", "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None  # 補齊

    # ---- 轉換 timestamp/timestr ----
    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")

    # ---- 日期過濾 ----
    if date_filter:
        df = df[df["time_str"].str.startswith(date_filter)]

    return df.sort_values("ts_dt")


def clear_history_db():
    tmp = Path(tempfile.gettempdir()) / "empty_history.sqlite"

    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS records (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               work_order TEXT,
               shift TEXT,
               device TEXT,
               timestamp REAL,
               time_str TEXT,
               temperature REAL,
               current REAL
           )"""
    )
    conn.commit()
    conn.close()

    return gh_upload_file(
        "Data/local/local_historical.db",
        tmp.read_bytes(),
        message="reset local_historical.db"
    )

# ============================================================
# UI - Real-time Page
# ============================================================

def realtime_page():
    st.header("实时数据")
    st.caption("每 5 秒自动刷新")

    if st_autorefresh:
        st_autorefresh(interval=5000, key="rt-refresh")

    df = load_realtime_db()
    if df.empty:
        st.info("尚无实时数据")
        return

    latest = df.sort_values("ts_dt").groupby("device").tail(1)
    devices = sorted(latest["device"].dropna().unique().tolist())
    runtimes = _compute_device_runtime(df)

    cols = st.columns(len(devices))
    for i, dev in enumerate(devices):
        sub = latest[latest["device"] == dev]

        temp = safe_float(sub["temperature"].values[0])
        curr = safe_float(sub["current"].values[0])

        with cols[i]:
            st.metric(f"{dev} Temperature (°C)", f"{temp:.1f}")
            st.metric(f"{dev} Current (A)", f"{curr:.2f}")
            st.metric(f"{dev} 运行时长", _format_duration(runtimes.get(dev, 0)))

    st.divider()

    st.subheader("设备快照")
    snap = latest.reset_index(drop=True).copy()
    snap["runtime"] = snap.apply(
        lambda r: _format_duration(runtimes.get(r["device"], 0)), axis=1
    )
    st.dataframe(snap)

    st.divider()
    st.subheader("趋势图")

    for dev in devices:
        series = df[df["device"] == dev].sort_values("ts_dt")
        if series.empty:
            continue

        series["temperature"] = series["temperature"].apply(safe_float)
        series["current"] = series["current"].apply(safe_float)

        c1, c2 = st.columns(2)
        with c1:
            st.line_chart(series.set_index("ts_dt")["temperature"], height=200)
        with c2:
            st.line_chart(series.set_index("ts_dt")["current"], height=200)

# ============================================================
# UI - History Page
# ============================================================

def history_page():
    st.header("历史数据")

    df_history = load_history_db()
    if df_history.empty:
        st.info("尚无历史数据")
        return

    dates_db = sorted(df_history["time_str"].str[:10].unique().tolist())
    sel = st.selectbox("选择日期", dates_db)

    df = load_history_db(date_filter=sel)
    if df.empty:
        st.info("没有记录")
        return

    orders = sorted(df["work_order"].dropna().unique().tolist())
    sel_order = st.selectbox("工单", orders)

    if sel_order:
        df = df[df["work_order"] == sel_order]

    runtimes = _compute_device_runtime(df)
    snapshot = df.sort_values("ts_dt").groupby("device").tail(1)

    snap = snapshot.reset_index(drop=True).copy()
    snap["runtime"] = snap.apply(
        lambda r: _format_duration(runtimes.get(r["device"], 0)), axis=1
    )
    st.dataframe(snap)

    st.divider()

    st.subheader("趋势图")
    for dev in sorted(df["device"].dropna().unique()):
        series = df[df["device"] == dev].sort_values("ts_dt")
        if series.empty:
            continue

        series["temperature"] = series["temperature"].apply(safe_float)
        series["current"] = series["current"].apply(safe_float)

        c1, c2 = st.columns(2)
        with c1:
            st.line_chart(series.set_index("ts_dt")["temperature"], height=200)
        with c2:
            st.line_chart(series.set_index("ts_dt")["current"], height=200)

    st.divider()
    st.subheader("维护工具")

    confirm = st.text_input("输入 DELETE 以清空 local_historical.db")
    if st.button("清空 history.db"):
        if confirm == "DELETE":
            ok = clear_history_db()
            if ok:
                st.success("历史 DB 已清空并上传到 GitHub")
            else:
                st.error("清空失败")
        else:
            st.warning("确认文字不正确")

# ============================================================
# MAIN ENTRY
# ============================================================

def main():
    st.title("EMS 管理台")

    page = st.sidebar.radio("页面", ["实时数据", "历史数据"])

    if page == "实时数据":
        realtime_page()
    else:
        history_page()

if __name__ == "__main__":
    main()




