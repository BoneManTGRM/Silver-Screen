"""Railway entry point for Streamlit and the private render viewer."""

import os

import streamlit as st

from silver_screen.render_preview import render_preview_routes


os.environ["SILVER_SCREEN_HTTP_RENDER_PREVIEW"] = "1"
app = st.App("streamlit_app.py", routes=render_preview_routes)
