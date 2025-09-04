# Default libraries

# Installed libraries
import streamlit as st

# Intrinsic packages
from lib.Navbar import Navbar

if __name__ == "__main__":
    Navbar()
    
    # Custom CSS for better styling
    st.markdown("""
    <style>
        .main-header {
            font-size: 2.5rem;
            color: #1f77b4;
            text-align: center;
            margin-bottom: 2rem;
        }
        .metric-container {
            background-color: #f0f2f6;
            padding: 1rem;
            border-radius: 10px;
            margin: 0.5rem;
        }
        .stAlert {
            margin: 1rem 0;
        }
    </style>
    """, unsafe_allow_html=True)
    
    # Set page configuration
    st.set_page_config(
        page_title="Project Management Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Description section
    st.subheader("Description")
    st.markdown(
        body="LOGOS is a software package which contains a set of discrete optimization models that can be employed for capital budgeting optimization problems. More specifically, provided a set of items (characterized by cost and reward values) and constraints, these models select the best combination of items which maximizes overall reward and satisfies the provided constraints. The developed models are based on different versions of the knapsack optimization algorithms. Two main classes of optimization models have been initially developed: deterministic and stochastic. Stochastic optimization models evolve deterministic models by explicitly considering data uncertainties (associated to constraints or item cost and reward). These models can be employed as stand-alone models or interfaced with the INL developed RAVEN code to propagate data uncertainties and analyze the generated data (i.e., sensitivity analysis).",
        width="content"
    )
    
    # Quick Start section
    st.subheader("Quick Start")
    st.markdown(
        body="Use the left menu bar to scroll through the different available modules for Gantt chart schedule viewing.",
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
    