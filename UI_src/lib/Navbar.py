# -*- coding: utf-8 -*-
"""
Created on Thu Sep  4 14:33:21 2025

@author: ChEdw
"""

import streamlit as st

def Navbar():
    with st.sidebar:
        st.page_link('Main.py', label="Home")
        st.page_link('pages/1_Schedule Viewer.py', label='Schedule Viewer')
        st.page_link('pages/2_Activity Organizer.py', label='Activity Organizer')
        st.page_link('pages/3_Group Manager.py', label='Group Manager')
        st.page_link('pages/4_Resource Manager.py', label='Resource Manager')
        st.page_link('pages/5_App Statistics.py', label='App Statistics')
        