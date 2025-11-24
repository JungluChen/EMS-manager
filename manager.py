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
# GitHub API Functions (Read + Write)
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

# 下載 GitHub Repo 的檔案（回傳 bytes）
def gh_download_file(path):
    owner, repo, branch = gh_owner_repo_branch()
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"

    r = requests.get(url, headers=gh_headers(), timeout=20)
    if r.status_code != 200:
        return None

    content = r.json().get("content", None)
    if not content:
        return None

    try:
        return base64.b64decode(content)
    except:
        return None

# 上傳（或覆蓋）GitHub Repo 的檔案
def gh_upload_file(path, content_bytes, message="update file"):
    owner, repo, branch = gh_owner_repo_branch()

    # 先取得 sha（若檔案存在）
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=gh_headers())

    sha = r.json().get("sha") if r.status_code == 200 else None

    b64 = base64.b64encode(content_bytes).decode("utf-8")

    payload = {
        "message": message,
        "content": b64,
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    put_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    pr = requests.put(put_url, headers=gh_headers(), json=payload)
    return pr.status_code in (200, 201)


# ============================================================
# Database Loading (memory only)
# ============================================================

def load_sqlite_from_bytes(db_bytes, date_filter=None):
    """將 GitHub 下載的 SQLite bytes 在 memory 中讀取"""
    tmp = Path(tempfile.gettempdir()) / "tmp_db.sqlite"
    tmp.write_bytes(db_bytes)

    try:
        conn = sqlite3.connect(str(tmp))
        cur = conn.cursor()

        if date_filter:
            cur.execute(
                "SELECT ts, data FROM records WHERE ts LIKE ? ORDER BY ts ASC",
                (date_filter + "%",),
            )
        else:
            cur.execute("SELECT ts, data FROM records ORDER BY ts ASC")

        rows = cur.fetchall()
        conn.close()
    except:
        return pd.DataFrame()

    recs = []
    for ts, data in rows:
        try:
            obj = json.loads(data)
        except:
            obj = {}

        recs.append({
            "ts": ts,
            "line": obj.get("line"),
            "shift": obj.get("shift"),
            "work_order": obj.get("work_order"),
            "temperature": obj.get("temperature"),
            "current": obj.get("current"),
        })

    df = pd.DataFrame(recs)
    if not df.empty:
        df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")

    return df


# ============================================================
# Real-time DB
# ============================================================

def load_realtime_db():
    db = gh_download_file("real_time_monitoring/temp/ems.db")
    if not db:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "realtime.sqlite"
    tmp.write_bytes(db)
    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute("SELECT id, ts, data FROM records ORDER BY id DESC LIMIT 1000")
        rows = cur.fetchall()
        conn.close()
    except:
        return pd.DataFrame()

    recs = []
    for rid, ts, data in rows:
        try:
            obj = json.loads(data)
        except:
            obj = {}
        recs.append({
            "id": rid,
            "ts": ts,
            "line": obj.get("line"),
            "shift": obj.get("shift"),
            "work_order": obj.get("work_order"),
            "temperature": obj.get("temperature"),
            "current": obj.get("current"),
        })

    df = pd.DataFrame(recs)
    if not df.empty:
        df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")

    return df


# ============================================================
# History DB (read/write)
# ============================================================

def load_history_db(date_filter=None):
    db = gh_download_file("historical_data/history.db")
    if not db:
        return pd.DataFrame()
    return load_sqlite_from_bytes(db, date_filter=date_filter)

def clear_history_db():
    """清空 history.db（上傳新的空 DB）"""
    tmp = Path(tempfile.gettempdir()) / "empty_history.sqlite"

    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS records (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts TEXT NOT NULL,
               data TEXT NOT NULL
           )"""
    )
    conn.commit()
    conn.close()

    return gh_upload_file(
        "historical_data/history.db",
        tmp.read_bytes(),
        message="reset history.db"
    )


# ============================================================
# Archive CSV
# ============================================================

def list_archives():
    owner, repo, branch = gh_owner_repo_branch()
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/historical_data/archives?ref={branch}"

    r = requests.get(url, headers=gh_headers(), timeout=15)
    if r.status_code != 200:
        return []

    files = r.json()
    return [f for f in files if f["name"].endswith(".csv")]


def load_archive_csv(name):
    csv_bytes = gh_download_file(f"historical_data/archives/{name}")
    if not csv_bytes:
        return pd.DataFrame()

    tmp = Path(tempfile.gettempdir()) / "tmp.csv"
    tmp.write_bytes(csv_bytes)

    df = pd.read_csv(tmp)
    if "data" in df.columns:
        parsed = df["data"].apply(lambda x: json.loads(x) if isinstance(x, str) else {})
        df_parsed = pd.json_normalize(parsed)
        for col in ["line", "shift", "work_order", "temperature", "current"]:
            if col in df_parsed.columns:
                df[col] = df_parsed[col]

    df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")
    return df


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

    latest = df.sort_values("ts_dt").groupby("line").tail(1)
    lines = sorted(latest["line"].dropna().unique().tolist())

    cols = st.columns(len(lines))

    for i, line in enumerate(lines):
        sub = latest[latest["line"] == line]
        temp = float(sub["temperature"].values[0])
        curr = float(sub["current"].values[0])

        with cols[i]:
            st.metric(f"{line} Temperature (°C)", f"{temp:.1f}")
            st.metric(f"{line} Current (A)", f"{curr:.2f}")

    st.divider()
    st.subheader("设备快照")
    st.dataframe(latest.reset_index(drop=True))

    st.divider()
    st.subheader("趋势")

    for line in lines:
        series = df[df["line"] == line].sort_values("ts_dt")
        if series.empty:
            continue

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

    archives = list_archives()
    dates_csv = [f["name"].replace(".csv", "") for f in archives]

    # history.db 日期
    df_history = load_history_db()
    if not df_history.empty:
        dates_db = sorted(df_history["ts"].str[:10].unique().tolist())
    else:
        dates_db = []

    names = sorted(set(dates_csv + dates_db))

    tab_view, tab_manage = st.tabs(["浏览", "维护"])

    with tab_view:
        sel = st.selectbox("选择日期", names)

        # 若 history.db 有該日期
        if sel in dates_db:
            df = load_history_db(date_filter=sel)
            src = "history.db"
        else:
            df = load_archive_csv(sel + ".csv")
            src = "archives/*.csv"

        if df.empty:
            st.info("没有记录")
            return

        st.caption(f"来源：{src}")

        # 工单过滤
        orders = sorted(df["work_order"].dropna().unique().tolist())
        sel_order = st.selectbox("工单", orders)

        if sel_order:
            df = df[df["work_order"] == sel_order]

        # 最新 snapshot
        snapshot = df.sort_values("ts_dt").groupby("line").tail(1)
        st.dataframe(snapshot.reset_index(drop=True))

        # 趋势图
        for line in sorted(df["line"].dropna().unique()):
            series = df[df["line"] == line].sort_values("ts_dt")
            if series.empty:
                continue

            c1, c2 = st.columns(2)
            with c1:
                st.line_chart(series.set_index("ts_dt")["temperature"], height=200)
            with c2:
                st.line_chart(series.set_index("ts_dt")["current"], height=200)

    with tab_manage:
        st.subheader("历史数据库维护")

        confirm = st.text_input("输入 DELETE 以确认清空 history.db")

        if st.button("清空 history.db"):
            if confirm != "DELETE":
                st.warning("确认文字不正确")
            else:
                ok = clear_history_db()
                if ok:
                    st.success("history.db 已清空并上传到 GitHub")
                else:
                    st.error("清空失败")


# ============================================================
# MAIN
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
