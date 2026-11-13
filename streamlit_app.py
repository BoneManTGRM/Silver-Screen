"""Silver Screen's home page and studio navigation."""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Silver Screen", page_icon="🎬", layout="wide", initial_sidebar_state="collapsed",
)

page = st.navigation({
    "Create": [
        st.Page("pages/0_Write_a_Movie.py", title="Movie and Ad Writer", default=True),
        st.Page("pages/0_Direct_Create.py", title="Direct Create"),
        st.Page("pages/0_Cast_Library.py", title="Cast Library"),
    ],
    "Plan and render": [
        st.Page("pages/9_Autonomous_Studio.py", title="Autonomous Studio"),
        st.Page("pages/7_Shot_Director.py", title="Shot Director"),
        st.Page("pages/6_Creative_Director.py", title="Creative Director"),
        st.Page("pages/0_Star_Vehicle_Studio.py", title="Star Vehicle Studio"),
        st.Page("pages/1_Full_Blueprint_Production.py", title="Full Blueprint Production"),
        st.Page("pages/2_Extend_Existing_Production.py", title="Extend Existing Production"),
    ],
    "Edit and finish": [
        st.Page("pages/2_Professional_Script_Sync.py", title="Professional Script Sync"),
        st.Page("pages/3_Voice_Studio.py", title="Voice Studio"),
        st.Page("pages/4_Cinematic_Continuity.py", title="Cinematic Continuity"),
        st.Page("pages/5_Director_Review.py", title="Director Review"),
        st.Page("pages/8_Visual_Quality_Supervisor.py", title="Visual Quality Supervisor"),
    ],
})
page.run()
