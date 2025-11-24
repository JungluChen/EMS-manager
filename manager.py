import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd
import streamlit as st
try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

ROOT = Path(__file__).parent
RT_TEMP = ROOT / 'real_time_monitoring' / 'temp'
DB_PATH = RT_TEMP / 'ems.db'
ARCHIVES_DIR = ROOT / 'historical_data' / 'archives'
VERSION_LOG = ROOT / 'historical_data' / 'version_log.json'
EXPORTS_DIR = ROOT / 'exports'
HISTORY_DB = ROOT / 'historical_data' / 'history.db'

st.set_page_config(page_title='EMS 管理台', layout='wide')

def load_db(limit=1000):
    if not DB_PATH.exists():
        return pd.DataFrame(columns=['id','ts','line','shift','work_order','temperature','current'])
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute('SELECT id, ts, data FROM records ORDER BY id DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    conn.close()
    recs = []
    for rid, ts, data in rows:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}
        recs.append({
            'id': rid,
            'ts': ts,
            'line': obj.get('line'),
            'shift': obj.get('shift'),
            'work_order': obj.get('work_order'),
            'temperature': obj.get('temperature'),
            'current': obj.get('current')
        })
    df = pd.DataFrame(recs)
    if not df.empty:
        try:
            df['ts_dt'] = pd.to_datetime(df['ts'], errors='coerce')
        except Exception:
            df['ts_dt'] = pd.NaT
    return df

def _list_archives():
    # Strict mode: only use historical_data archives
    return sorted(ARCHIVES_DIR.glob('*.csv'), key=lambda x: x.stem)

def load_history_db(date_str=None):
    if not HISTORY_DB.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(str(HISTORY_DB))
        cur = conn.cursor()
        if date_str:
            # Filter by date part of ts (prefix match)
            cur.execute("SELECT ts, data FROM records WHERE ts LIKE ? ORDER BY ts ASC", (date_str + '%',))
        else:
            cur.execute("SELECT ts, data FROM records ORDER BY ts ASC")
        rows = cur.fetchall(); conn.close()
        recs = []
        for ts, data in rows:
            try:
                obj = json.loads(data) if isinstance(data, str) else {}
            except Exception:
                obj = {}
            # normalize schema
            recs.append({
                'ts': ts,
                'line': obj.get('line'),
                'shift': obj.get('shift'),
                'work_order': obj.get('work_order'),
                'temperature': obj.get('temperature'),
                'current': obj.get('current')
            })
        df = pd.DataFrame(recs)
        if not df.empty:
            df['ts_dt'] = pd.to_datetime(df['ts'], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame()

def load_archive(date_str=None):
    files = _list_archives()
    if not files:
        return pd.DataFrame(), files, None
    target = None
    if date_str:
        for p in files:
            if p.stem == date_str:
                target = p
                break
    if target is None:
        target = files[-1]
    try:
        df = pd.read_csv(target)
        if 'data' in df.columns:
            parsed = df['data'].apply(lambda x: json.loads(x) if isinstance(x, str) else {})
            df_parsed = pd.json_normalize(parsed)
            for col in ['line','shift','work_order','temperature','current']:
                if col in df_parsed.columns:
                    df[col] = df_parsed[col]
        df['ts_dt'] = pd.to_datetime(df['ts'], errors='coerce')
        return df, files, target
    except Exception:
        return pd.DataFrame(), files, target

def realtime_page():
    st.header('实时数据')
    st.caption('每 5 秒自动刷新')
    if st_autorefresh:
        st_autorefresh(interval=5_000, key='rt-refresh')
    else:
        if st.button('立即刷新'):
            try:
                st.rerun()
            except Exception:
                try:
                    st.experimental_rerun()
                except Exception:
                    pass
    st.caption(f"来源：{DB_PATH}")
    df = load_db(limit=1000)
    if df.empty:
        st.info('暂无实时数据')
        return
    latest = df.sort_values('ts_dt').groupby('line').tail(1)
    lines = sorted(latest['line'].dropna().unique().tolist())
    cols = st.columns(max(1, len(lines))) if lines else st.columns(1)
    for i, line in enumerate(lines or ['N/A']):
        sub = latest[latest['line'] == line]
        temp = float(sub['temperature'].values[0]) if not sub.empty else 0.0
        curr = float(sub['current'].values[0]) if not sub.empty else 0.0
        with cols[i]:
            st.metric(label=f'{line} Temperature (°C)', value=f'{temp:.1f}')
            st.metric(label=f'{line} Current (A)', value=f'{curr:.2f}')
    st.divider()
    with st.expander('设备快照', expanded=True):
        st.dataframe(latest[['line','shift','work_order','ts','temperature','current']].reset_index(drop=True))
    st.divider()
    st.subheader('趋势')
    for line in lines:
        series = df[df['line'] == line].sort_values('ts_dt')
        if series.empty:
            continue
        c1, c2 = st.columns(2)
        with c1:
            st.line_chart(series.set_index('ts_dt')['temperature'], height=200)
        with c2:
            st.line_chart(series.set_index('ts_dt')['current'], height=200)

def historical_page():
    st.header('历史数据')
    dates = []
    if HISTORY_DB.exists():
        try:
            conn = sqlite3.connect(str(HISTORY_DB))
            cur = conn.cursor(); cur.execute("SELECT SUBSTR(ts,1,10) AS d FROM records GROUP BY d ORDER BY d")
            dates = [r[0] for r in cur.fetchall()]; conn.close()
        except Exception:
            dates = []
    files = _list_archives() if not dates else []
    names = dates if dates else [f.stem for f in files]
    tab_view, tab_manage = st.tabs(["浏览", "维护"]) 
    with tab_view:
        sel = st.selectbox('归档日期', names, index=(len(names)-1) if names else 0)
        if HISTORY_DB.exists():
            df = load_history_db(sel if names else None)
            target = HISTORY_DB
        else:
            df, _, target = load_archive(sel if names else None)
        if df.empty:
            st.info('没有可用归档')
            return
        if target:
            st.caption(f"来源：{target}")
        orders = sorted([x for x in df['work_order'].dropna().unique().tolist()]) if 'work_order' in df.columns else []
        sel_order = st.selectbox('工单', orders, index=0 if orders else None)
        if sel_order:
            df = df[df['work_order'] == sel_order]
        snapshot = df.sort_values('ts_dt').groupby('line').tail(1)
        st.dataframe(snapshot[['line','shift','work_order','ts','temperature','current']].reset_index(drop=True))
        lines = sorted(df['line'].dropna().unique().tolist())
        for line in lines:
            series = df[df['line'] == line].sort_values('ts_dt')
            if series.empty:
                continue
            c1, c2 = st.columns(2)
            with c1:
                st.line_chart(series.set_index('ts_dt')['temperature'], height=200)
            with c2:
                st.line_chart(series.set_index('ts_dt')['current'], height=200)
    with tab_manage:
        st.caption(f"数据库：{HISTORY_DB}")
        confirm = st.text_input('输入 DELETE 以确认')
        if st.button('清空历史数据库'):
            if confirm == 'DELETE':
                ok = False
                try:
                    conn = sqlite3.connect(str(HISTORY_DB))
                    cur = conn.cursor(); cur.execute('DELETE FROM records'); conn.commit(); cur.execute('VACUUM'); conn.commit(); conn.close()
                    ok = True
                except Exception:
                    ok = False
                if ok:
                    st.success('历史数据库已清空')
                else:
                    st.error('清空历史数据库失败')
            else:
                st.warning('确认文本不匹配')

def main():
    st.title('EMS 管理台')
    page = st.sidebar.radio('页面', ['实时数据', '历史数据'], index=0)
    if page == '实时数据':
        realtime_page()
    else:
        historical_page()

if __name__ == '__main__':
    main()
