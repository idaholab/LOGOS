# -*- coding: utf-8 -*-
"""
Created on Wed Aug 27 16:35:47 2025

@author: ChEdw
"""

# Default libraries

# Installed libraries
import streamlit as st

# Intrinsic packages
from lib.Navbar import Navbar

st.title("")
st.markdown("---")

if __name__ == "__main__":
    Navbar()
        
    st.title("🔧 Resource Manager")
    
    # Description section
    st.subheader("Description")
    st.markdown(
        body="Placeholder, No content",
        width="content"
    )
    
    
    # Footer
    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center; color: #666;'>
            <strong>Project Management Dashboard</strong> - Powered by Streamlit & PostgreSQL<br>
            Built with Intelligence for Windows Docker Environment
        </div>
        """, 
        unsafe_allow_html=True
    )