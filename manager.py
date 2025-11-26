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

@st.cache_data(show_spinner=False)
def gh_headers():
    return {
        "Authorization": f"Bearer {GIT_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

@st.cache_data(ttl=5)
def gh_download_file(path):
    """下載 GitHub 上 EMS 的檔案 bytes（含穩健網路處理）。回傳 (bytes, status)。"""
    try:
        url = f"https://api.github.com/repos/{GIT_OWNER}/{GIT_REPO}/contents/{path}?ref={GIT_BRANCH}"
        r = requests.get(url, headers=gh_headers(), timeout=15)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        j = r.json()
        content = j.get("content")
        if isinstance(content, str):
            try:
                return base64.b64decode(content), "ok"
            except Exception:
                pass
        # fallback 使用 download_url 直接抓 raw
        raw_url = j.get("download_url")
        if raw_url:
            rr = requests.get(raw_url, timeout=15)
            if 200 <= rr.status_code < 300:
                return rr.content, "ok"
            return None, f"raw HTTP {rr.status_code}"
        return None, "no-content"
    except Exception as e:
        return None, f"exception: {e.__class__.__name__}"

# ============================================================
# SQLite 通用讀取（標準化欄位）
# ============================================================
@st.cache_data(ttl=5)
def load_sqlite_bytes(db_bytes):
    if not db_bytes:
        return pd.DataFrame()
    tmp = Path(tempfile.gettempdir()) / "ems_tmp.sqlite"
    try:
        tmp.write_bytes(db_bytes)
    except Exception:
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(tmp)
        df = pd.read_sql_query("SELECT * FROM records", conn)
        conn.close()
    except Exception:
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
    for col in ["id", "work_order", "shift", "device", "timestamp", "time_str", "temperature", "current"]:
        if col not in df.columns:
            df[col] = None
    df["ts_dt"] = pd.to_datetime(df["time_str"], errors="coerce")
    df = df.sort_values("ts_dt")
    return df

# ============================================================
# 📡 實時資料（每 5 秒局部更新 + 圖表）
# ============================================================
def init_rt_state():
    if "rt_last_refresh" not in st.session_state:
        st.session_state["rt_last_refresh"] = datetime.now()
    if "rt_start_time" not in st.session_state:
        st.session_state["rt_start_time"] = datetime.now()
    if "rt_prev_max_ts" not in st.session_state:
        st.session_state["rt_prev_max_ts"] = None
    if "rt_cached_df" not in st.session_state:
        st.session_state["rt_cached_df"] = pd.DataFrame()

@st.fragment(run_every=5)
def realtime_page():
    init_rt_state()
    with st.spinner("讀取即時資料中..."):
        db_bytes, status = gh_download_file("Data/local/local_realtime.db")
        df = load_sqlite_bytes(db_bytes)
    st.caption(f"資料來源狀態：{status}")
    if df.empty:
        st.info("尚無即時資料")
        base_df = pd.DataFrame({"ts_dt": [pd.Timestamp.now()], "type": ["temperature"], "value": [None]})
        base_chart = (
            alt.Chart(base_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("ts_dt:T", title="時間", axis=alt.Axis(format="%Y/%m/%d %H:%M:%S", tickCount=10, labelAngle=45)),
                y=alt.Y("value:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("type:N", legend=alt.Legend(orient="top", title="類別"), scale=alt.Scale(domain=["current", "temperature"], range=["#3498db", "#e74c3c"]))
            )
            .properties(height=350)
        )
        st.altair_chart(base_chart, use_container_width=True)
        return
    if df.dropna(subset=["ts_dt"]).empty:
        st.info("資料時間欄位為空，暫無可視化")
        base_df = pd.DataFrame({"ts_dt": [pd.Timestamp.now()], "type": ["temperature"], "value": [None]})
        base_chart = (
            alt.Chart(base_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("ts_dt:T", title="時間", axis=alt.Axis(format="%Y/%m/%d %H:%M:%S", tickCount=10, labelAngle=45)),
                y=alt.Y("value:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
                color=alt.Color("type:N", legend=alt.Legend(orient="top", title="類別"), scale=alt.Scale(domain=["current", "temperature"], range=["#3498db", "#e74c3c"]))
            )
            .properties(height=350)
        )
        st.altair_chart(base_chart, use_container_width=True)
        return
    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["current"] = pd.to_numeric(df["current"], errors="coerce")
    max_ts = None
    try:
        max_ts = df["ts_dt"].max()
    except Exception:
        max_ts = None
    if max_ts and (st.session_state["rt_prev_max_ts"] is None or max_ts > st.session_state["rt_prev_max_ts"]):
        st.session_state["rt_prev_max_ts"] = max_ts
        st.session_state["rt_last_refresh"] = datetime.now()
        if st.session_state["rt_start_time"] is None:
            st.session_state["rt_start_time"] = datetime.now()
        st.session_state["rt_cached_df"] = df.copy()
    else:
        # 使用上一次成功資料以提升穩定性
        if not st.session_state["rt_cached_df"].empty:
            df = st.session_state["rt_cached_df"].copy()
    elapsed_sec = int((datetime.now() - st.session_state["rt_start_time"]).total_seconds()) if st.session_state.get("rt_start_time") else 0
    elapsed_str = datetime.utcfromtimestamp(elapsed_sec).strftime("%H:%M:%S")
    active = True if max_ts else False
    color = "#22c55e" if active else "#ef4444"
    st.markdown(f"<div>狀態：<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:{color};margin-right:6px;'></span>{'紀錄中' if active else '未紀錄'}</div>", unsafe_allow_html=True)
    col_run, col_temp, col_curr = st.columns(3)
    with col_run:
        st.write("運行時間", elapsed_str)
    with col_temp:
        if df["temperature"].notna().any():
            try:
                st.write("🌡 溫度", f"{df['temperature'].dropna().iloc[-1]} °C")
            except Exception:
                pass
    with col_curr:
        if df["current"].notna().any():
            try:
                st.write("⚡ 電流", f"{df['current'].dropna().iloc[-1]} A")
            except Exception:
                pass
    df_plot = df.dropna(subset=["ts_dt"]).copy()
    df_melt = df_plot.melt(id_vars=["ts_dt"], value_vars=["temperature", "current"], var_name="type", value_name="value")
    df_melt = df_melt.dropna(subset=["value"])
    chart = (
        alt.Chart(df_melt)
        .mark_line(interpolate="linear", point=True)
        .encode(
            x=alt.X("ts_dt:T", title="時間", axis=alt.Axis(format="%Y/%m/%d %H:%M:%S", tickCount=10, labelAngle=45)),
            y=alt.Y("value:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("type:N", legend=alt.Legend(orient="top", title="類別"), scale=alt.Scale(domain=["current", "temperature"], range=["#3498db", "#e74c3c"])),
            tooltip=["ts_dt:T", "type", "value"],
        )
        .properties(height=350)
    )
    st.altair_chart(chart, use_container_width=True)

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
            chart_temp = (
                alt.Chart(dev_df.dropna(subset=["ts_dt"]))
                .mark_line(interpolate="linear", point=True)
                .encode(
                    x=alt.X("ts_dt:T", title="時間", axis=alt.Axis(format="%Y/%m/%d %H:%M:%S", tickCount=10, labelAngle=45)),
                    y=alt.Y("temperature:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
                    color=alt.value("#e74c3c"),
                    tooltip=["ts_dt:T", "temperature"],
                )
                .properties(height=250)
            )
            st.altair_chart(chart_temp, use_container_width=True)

        with c2:
            st.write("Current")
            chart_curr = (
                alt.Chart(dev_df.dropna(subset=["ts_dt"]))
                .mark_line(interpolate="linear", point=True)
                .encode(
                    x=alt.X("ts_dt:T", title="時間", axis=alt.Axis(format="%Y/%m/%d %H:%M:%S", tickCount=10, labelAngle=45)),
                    y=alt.Y("current:Q", title="數值", scale=alt.Scale(domain=[0, 100])),
                    color=alt.value("#3498db"),
                    tooltip=["ts_dt:T", "current"],
                )
                .properties(height=250)
            )
            st.altair_chart(chart_curr, use_container_width=True)

# ============================================================
# Main
# ============================================================
page = st.sidebar.radio("選單", ["實時資料", "歷史資料"])
st.session_state["current_page"] = page

if page == "實時資料":
    realtime_page()
else:
    history_page()
