import streamlit as st
import requests

owner = st.secrets["GIT_OWNER"]
repo = st.secrets["GIT_REPO"]
branch = st.secrets.get("GIT_BRANCH", "main")
token = st.secrets["GIT_TOKEN"]

url = f"https://api.github.com/repos/{owner}/{repo}/contents/Data/local?ref={branch}"

st.write("Test URL:", url)

headers = {"Authorization": f"Bearer {token}"}

r = requests.get(url, headers=headers)

st.write("HTTP Status:", r.status_code)

if r.status_code == 200:
    st.write("Files under Data/local:")
    st.json(r.json())
else:
    st.error("❌ Cannot list directory — wrong repo or wrong path!")
