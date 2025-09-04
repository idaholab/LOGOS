# -*- coding: utf-8 -*-
"""
Created on Wed Aug 27 16:35:47 2025

@author: ChEdw
"""
# Default libraries
from datetime import datetime, timedelta
import numpy as np

# Installed libraries
import streamlit as st
import pandas as pd
import plotly.express as px

# Intrinsic packages
from lib.db import init_database
from lib.Navbar import Navbar

def load_gantt_data(engine):
    """Load data from database for Gantt chart"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            query = """
            SELECT 
                id,
                activity_name,
                description,
                start_date,
                end_date,
                resources,
                dependencies,
                critical_path,
                group_name
            FROM project_activities 
            ORDER BY start_date, activity_name;
            """
            
            df = pd.read_sql(query, engine)
            
            # Convert JSONB columns back to lists
            if not df.empty:
                df['resources'] = df['resources'].apply(lambda x: x if isinstance(x, list) else [])
                df['dependencies'] = df['dependencies'].apply(lambda x: x if isinstance(x, list) else [])
                
                # Ensure datetime objects for dates
                df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
                df['end_date'] = pd.to_datetime(df['end_date'], errors='coerce')
                
                # Remove rows with invalid dates
                invalid_dates = df['start_date'].isna() | df['end_date'].isna()
                if invalid_dates.any():
                    st.warning(f"⚠️ Removed {invalid_dates.sum()} activities with invalid dates")
                    df = df[~invalid_dates]
            
            return df, "postgresql"
            
        except Exception as e:
            st.error(f"Error loading from PostgreSQL: {str(e)}")
            return pd.DataFrame(), "error"
    
    elif st.session_state.get('database_type') == 'session':
        # Load from session state backup database
        if 'temp_database' in st.session_state:
            df = st.session_state.temp_database.copy()
            
            # Ensure datetime objects for dates
            if not df.empty:
                df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
                df['end_date'] = pd.to_datetime(df['end_date'], errors='coerce')
                
                # Remove rows with invalid dates
                invalid_dates = df['start_date'].isna() | df['end_date'].isna()
                if invalid_dates.any():
                    st.warning(f"⚠️ Removed {invalid_dates.sum()} activities with invalid dates")
                    df = df[~invalid_dates]
            
            return df, "session"
        
        return pd.DataFrame(), "session_empty"
    
    return pd.DataFrame(), "no_connection"

def create_gantt_chart(df, show_critical_path=True, show_dependencies=True, group_filter=None, time_scale="days"):
    """Create interactive Gantt chart with dependencies"""
    
    if df.empty:
        st.warning("No data available to create Gantt chart")
        return None
    
    # Validate datetime objects
    if not pd.api.types.is_datetime64_any_dtype(df['start_date']):
        st.error("❌ Start dates are not in datetime format")
        return None
    
    if not pd.api.types.is_datetime64_any_dtype(df['end_date']):
        st.error("❌ End dates are not in datetime format") 
        return None
    
    # Check for invalid date ranges
    invalid_ranges = df['start_date'] > df['end_date']
    if invalid_ranges.any():
        st.warning(f"⚠️ Found {invalid_ranges.sum()} activities with start date after end date")
        df = df[~invalid_ranges]
        if df.empty:
            st.error("❌ No valid activities remaining after date validation")
            return None
    
    # Filter data if group filter is applied
    if group_filter and group_filter != 'All':
        df = df[df['group_name'] == group_filter].copy()
    
    if df.empty:
        st.warning(f"No activities found for group: {group_filter}")
        return None
    
    # Prepare data for Gantt chart
    gantt_data = []
    activity_positions = {}  # Store y-positions for dependency arrows
    
    for idx, row in df.iterrows():
        # Ensure we have datetime objects for calculation
        if not isinstance(row['start_date'], (pd.Timestamp, datetime)):
            st.error(f"❌ Invalid start date for activity: {row['activity_name']}")
            continue
            
        if not isinstance(row['end_date'], (pd.Timestamp, datetime)):
            st.error(f"❌ Invalid end date for activity: {row['activity_name']}")
            continue
        
        # Calculate duration
        duration = (row['end_date'] - row['start_date']).days
        
        # Skip activities with zero or negative duration
        if duration <= 0:
            st.warning(f"⚠️ Skipping activity '{row['activity_name']}' with invalid duration: {duration} days")
            continue
        
        # Color based on critical path
        color = '#FF6B6B' if row['critical_path'] and show_critical_path else '#4ECDC4'
        
        # Create hover text with details
        hover_text = f"""
        <b>{row['activity_name']}</b><br>
        Group: {row['group_name']}<br>
        Duration: {duration} days<br>
        Start: {row['start_date'].strftime('%Y-%m-%d')}<br>
        End: {row['end_date'].strftime('%Y-%m-%d')}<br>
        Critical Path: {'Yes' if row['critical_path'] else 'No'}<br>
        Resources: {', '.join(row['resources']) if row['resources'] else 'None'}<br>
        Dependencies: {', '.join(row['dependencies']) if row['dependencies'] else 'None'}
        """
        
        gantt_data.append({
            'Task': row['activity_name'],
            'Start': row['start_date'],
            'Finish': row['end_date'],
            'Resource': row['group_name'],
            'Complete': 100 if datetime.now().date() > row['end_date'].date() else 
                       max(0, min(100, (datetime.now().date() - row['start_date'].date()).days / duration * 100)) if duration > 0 else 0,
            'Description': hover_text,
            'Critical': row['critical_path'],
            'ID': row['id'],
            'Dependencies': row['dependencies']
        })
        
        # Store position for dependency arrows
        activity_positions[row['activity_name']] = len(gantt_data) - 1
        
    # Check if we have valid data after processing
    if not gantt_data:
        st.error("❌ No valid activities found after date validation")
        return None
    
    # Convert to DataFrame
    gantt_df = pd.DataFrame(gantt_data)
    
    # Create Gantt chart
    fig = px.timeline(
        gantt_df,
        x_start="Start",
        x_end="Finish",
        y="Task",
        color="Resource",
        hover_data={'Description': True, 'Complete': ':.1f%'},
        height=max(400, len(gantt_df) * 40 + 100)
    )
    
    # Reverse the y-axis to show first activity at top
    fig.update_layout(yaxis={'autorange': 'reversed'})
    
    # Customize appearance
    fig.update_traces(
        hovertemplate='%{customdata[0]}<extra></extra>',
        customdata=gantt_df[['Description']].values
    )
    
    # Calculate tick values based on time scale
    project_start = gantt_df['Start'].min()
    project_end = gantt_df['Finish'].max()
   
    if time_scale == "days":
       # Daily ticks - show every few days based on project duration
       duration_days = (project_end - project_start).days
       if duration_days <= 30:
           tick_freq = timedelta(days=1)
       elif duration_days <= 90:
           tick_freq = timedelta(days=7)  # Weekly
       else:
           tick_freq = timedelta(days=30)  # Monthly
           
       tickvals = pd.date_range(start=project_start, end=project_end, freq=tick_freq)
       tickformat = "%m/%d"
       
    elif time_scale == "months":
       # Monthly ticks
       tickvals = pd.date_range(start=project_start.replace(day=1), 
                               end=project_end + pd.DateOffset(months=1), 
                               freq='MS')  # Month start
       tickformat = "%b %Y"
       
    elif time_scale == "years":
       # Yearly ticks
       tickvals = pd.date_range(start=project_start.replace(month=1, day=1), 
                               end=project_end + pd.DateOffset(years=1), 
                               freq='YS')  # Year start
       tickformat = "%Y"  

    tickvals_y   = np.arange(0, len(gantt_df))
   
   # Update layout with custom ticks and reversed y-axis
    fig.update_layout(
       xaxis_title="Timeline",
       yaxis_title="Activities",
       yaxis={
           'autorange': 'reversed',
           'ticktext' : gantt_df['Task'],
           'tickvals' : tickvals_y,
           'range'    : [tickvals_y[0]-1/2, tickvals_y[-1]+1/2],
           'showgrid' : True,
           'gridcolor': 'rgba(128, 128, 128, 0.3)',
           'gridwidth': 1
       },
       xaxis={
           'tickvals'  : tickvals,
           'tickformat': tickformat,
           'tickangle' : -45 if time_scale == "days" else 0,
           'showgrid'  : True,
           'gridcolor' : 'rgba(128, 128, 128, 0.3)',
           'gridwidth' : 1
       },
       showlegend=True,
       hovermode='closest',
       font=dict(size=12),
       plot_bgcolor='white',
       paper_bgcolor='white'
    )   
   
    # Add critical path highlighting if enabled (adjusted for reversed y-axis)
    if show_critical_path:
        for idx, row in gantt_df.iterrows():
            if row['Critical']:
                fig.add_shape(
                    type="rect",
                    x0=row['Start'],
                    x1=row['Finish'],
                    y0=idx - 0.4,
                    y1=idx + 0.4,
                    line=dict(color="red", width=2),
                    fillcolor="rgba(255, 107, 107, 1)"
                )
    
    # Add dependency arrows if enabled
    if show_dependencies:
        fig = add_dependency_arrows(fig, gantt_df, show_critical_path)
    
    # # Add today's date line
    # today = datetime.now()
    # fig.add_vline(
    #     x=today,
    #     line_dash="dash",
    #     line_color="red",
    #     annotation_text="Today",
    #     annotation_position="top"
    # )
    
    return fig

def add_dependency_arrows(fig, gantt_df, show_critical_path=False):
   """Add dependency arrows with 90-degree bends to Gantt chart"""
   new_fig = fig
   
   for idx, row in gantt_df.iterrows():
        start_act = row
        
        if start_act['Dependencies']:
            # For each activity linked to current activity
            for endPoint in start_act['Dependencies']:
                ind     = gantt_df.index[gantt_df["Task"]==endPoint].tolist()
                end_act = gantt_df.loc[ind[0]] 
                if not show_critical_path:
                    new_fig = draw_arrow_between_jobs_v2(new_fig, start_act, end_act)
                elif show_critical_path and (end_act['Critical'] and start_act['Critical']):
                    new_fig = draw_arrow_between_jobs_v2(new_fig, start_act, end_act, color="rgba(100, 100, 100, 0.8)", width=3)
   
   return fig

def draw_arrow_between_jobs_v2(fig, first_job_dict, second_job_dict, color="blue", width=2):
    '''
    Draws an arrow between two gantt activities on a plotly chart. Requires timing
    of the activity to determine start and end spots. 
    
    Arrow starts from middle bottom of first activity and ends at the beginning of
    the next activity.

    Parameters
    ----------
    fig : plotly.figure
        Figure to draw the arrows on.
    first_job_dict : dict
        Dict of the starting arrow event.
        {Task:<string>,
         Start:<datetime>,
         Finish:<datetime>}
    second_job_dict : dict
        Dict of the ending arrow event. Same type as first_job_dict.
    color : string (Optional)
        Color of line. The default is "blue".
    width : int (Optional)
        Line width. The default is 2.
    
    Returns
    -------
    fig : plotly.figure
        New figure with arrow drawn.

    '''

    ## retrieve tick text and tick vals
    job_yaxis_mapping = dict(zip(fig.layout.yaxis.ticktext,fig.layout.yaxis.tickvals))
    jobs_x_delta      = first_job_dict['Finish'] - first_job_dict['Start']
    jobs_y_delta      = job_yaxis_mapping[first_job_dict['Task']] - job_yaxis_mapping[second_job_dict['Task']]

    ## vertical line segment
    if jobs_y_delta < 0:
        fig = draw_line(fig=fig,
                        x0=first_job_dict['Finish'] - jobs_x_delta/2,
                        x1=first_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']] ,
                        y1=job_yaxis_mapping[second_job_dict['Task']],
                        color=color,
                        width=width
        )
    elif jobs_y_delta >= 0:
        fig = draw_line(fig=fig,
                        x0=first_job_dict['Finish'] - jobs_x_delta/2,
                        x1=first_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']] - 1/2,
                        y1=job_yaxis_mapping[second_job_dict['Task']],
                        color=color,
                        width=width
        )        

    ## horizontal line segment
    fig = draw_line(fig=fig,
                    x0=first_job_dict['Finish'] - jobs_x_delta/2,
                    x1=second_job_dict['Start'],
                    y0=job_yaxis_mapping[second_job_dict['Task']],
                    y1=job_yaxis_mapping[second_job_dict['Task']],
                    color=color,
                    width=width
    )
    

    ## draw an arrow
    fig.add_annotation(
        x=second_job_dict['Start'], 
        y=job_yaxis_mapping[second_job_dict['Task']],
        xref="x",yref="y",
        showarrow=True,
        ax=-10,
        ay=0,
        arrowwidth=3,
        arrowcolor=color,
        arrowhead=3
    )
    return fig

def draw_line(fig, x0, x1, y0, y1, color="blue", width=2):
    '''
    Draws a line between 2 coordinate points.

    Parameters
    ----------
    fig : plotly figure
        Figure to draw line on.
    x0 : object
        Start x coordinate.
    x1 : object
        End x coordinate.
    y0 : object
        Start y coordinate.
    y1 : object
        End y coordinate.
    color : string (Optional)
        Color of line. The default is "blue".
    width : int (Optional)
        Line width. The default is 2.

    Returns
    -------
    fig : plotly figure
        Figure with lines

    '''
    ## horizontal line segment
    fig.add_shape(
        x0=x0, y0=y0, 
        x1=x1, y1=y1,
        line=dict(color=color, width=width)
    )
    
    return fig   

def calculate_project_stats(df):
    """Calculate project statistics"""
    if df.empty:
        return {}
    
    stats = {}
    
    # Basic stats
    stats['total_activities'] = len(df)
    stats['critical_path_count'] = len(df[df['critical_path'] == True])
    stats['unique_groups'] = df['group_name'].nunique()
    
    # Date stats
    stats['project_start'] = df['start_date'].min()
    stats['project_end'] = df['end_date'].max()
    stats['project_duration'] = (stats['project_end'] - stats['project_start']).days
    
    # Progress stats
    today = datetime.now().date()
    stats['completed_activities'] = len(df[df['end_date'].dt.date < today])
    stats['in_progress_activities'] = len(df[
        (df['start_date'].dt.date <= today) & 
        (df['end_date'].dt.date >= today)
    ])
    stats['future_activities'] = len(df[df['start_date'].dt.date > today])
    
    # Calculate overall progress
    if stats['project_duration'] > 0:
        elapsed_days = (today - stats['project_start'].date()).days
        stats['overall_progress'] = max(0, min(100, (elapsed_days / stats['project_duration']) * 100))
    else:
        stats['overall_progress'] = 0
    
    return stats

# Main Gantt Chart Page
def gantt_chart_page(engine):
    """Main function for the Gantt Chart page"""
    
    st.title("📊 Project Gantt Chart")
    st.markdown("Interactive timeline visualization of project activities with dependencies")
                
    # Load data
    df, data_source = load_gantt_data(engine)
    
    # Database status
    if data_source == "postgresql":
        st.success("✅ Data loaded from PostgreSQL database")
    elif data_source == "session":
        st.warning("⚠️ Data loaded from session backup database")
    elif data_source == "session_empty":
        st.error("❌ Session database is empty")
    elif data_source == "error":
        st.error("❌ Error loading database data")
    elif data_source == "no_connection":
        st.error("❌ No database connection available")
    
    if not df.empty:
        # Calculate and display project statistics
        stats = calculate_project_stats(df)
        
        if stats:
            st.subheader("📈 Project Overview")
            
            col1, col2, col3, col4, col5 = st.columns(5)
            
            with col1:
                st.metric("Total Activities", stats['total_activities'])
            
            with col2:
                st.metric("Critical Path", stats['critical_path_count'])
            
            with col3:
                st.metric("Project Duration", f"{stats['project_duration']} days")
            
            with col4:
                st.metric("Overall Progress", f"{stats['overall_progress']:.1f}%")
            
            with col5:
                st.metric("Active Groups", stats['unique_groups'])
            
            # Progress breakdown
            progress_col1, progress_col2, progress_col3 = st.columns(3)
            
            with progress_col1:
                st.metric("Completed", stats['completed_activities'], 
                         delta=f"{(stats['completed_activities']/stats['total_activities']*100):.1f}%")
            
            with progress_col2:
                st.metric("In Progress", stats['in_progress_activities'],
                         delta=f"{(stats['in_progress_activities']/stats['total_activities']*100):.1f}%")
            
            with progress_col3:
                st.metric("Future", stats['future_activities'],
                         delta=f"{(stats['future_activities']/stats['total_activities']*100):.1f}%")
        
        st.markdown("---")
        
        # Chart controls
        st.subheader("🎛️ Chart Controls")
        
        control_col1, control_col2, control_col3, control_col4, control_col5 = st.columns(5)
        
        with control_col1:
            show_critical_path = st.checkbox("🔴 Highlight Critical Path", value=True,
                                           help="Highlight activities on the critical path")
        
        with control_col2:
            show_dependencies = st.checkbox("➡️ Show Dependencies", value=True,
                                          help="Show arrows indicating task dependencies")
        
        with control_col3:
            # Group filter
            groups = ['All'] + sorted(df['group_name'].unique().tolist())
            group_filter = st.selectbox("Filter by Group", groups)
        
        with control_col4:
            # Time scale selector
            time_scale = st.selectbox("Time Scale", 
                                options=["days", "months", "years"],
                                help="Choose the time scale for x-axis ticks")
                
        with control_col5:
            if st.button("🔄 Refresh Data"):
                st.cache_data.clear()
                st.rerun()
        
        st.markdown("---")
        
        # Generate and display Gantt chart
        st.subheader("📊 Interactive Gantt Chart")
        
        with st.spinner("Generating Gantt chart..."):
            fig = create_gantt_chart(df, show_critical_path, show_dependencies, group_filter, time_scale)
            
            if fig:
                st.plotly_chart(fig, use_container_width=True)
                
                # Chart legend and help
                with st.expander("📖 Chart Guide"):
                    st.markdown("""
                    ### How to Read the Gantt Chart:
                    
                    **📊 Chart Elements:**
                    - **Horizontal bars**: Represent activity duration
                    - **Colors**: Different colors represent different groups/teams
                    - **Red highlighting**: Critical path activities (if enabled)
                    - **Arrows**: Show dependencies between activities
                    - **Red dashed line**: Current date (Today)
                    
                    **🖱️ Interactive Features:**
                    - **Hover**: View detailed activity information
                    - **Zoom**: Use mouse wheel or zoom controls
                    - **Pan**: Click and drag to move around
                    - **Legend**: Click to hide/show groups
                    
                    **🔴 Critical Path:**
                    Activities that directly impact the project end date. Delays in critical path activities will delay the entire project.
                    
                    **➡️ Dependencies:**
                    Arrows show which activities must complete before others can start. Follow the arrows to understand the project flow.
                    
                    **📈 Progress Indicators:**
                    - Activities past the red line are overdue if not completed
                    - Activities crossing the red line are currently in progress
                    - Activities after the red line are scheduled for the future
                    """)
                
                # Export options
                st.subheader("📥 Export Options")
                export_col1, export_col2 = st.columns(2)
                
                with export_col1:
                    # Export chart as HTML
                    html_str = fig.to_html(include_plotlyjs='cdn')
                    st.download_button(
                        label="📊 Download Chart (HTML)",
                        data=html_str,
                        file_name=f"gantt_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                        mime="text/html"
                    )
                
                with export_col2:
                    # Export data as CSV
                    if group_filter and group_filter != 'All':
                        export_df = df[df['group_name'] == group_filter].copy()
                    else:
                        export_df = df.copy()
                    
                    # Format for export
                    export_df['dependencies'] = export_df['dependencies'].apply(
                        lambda x: ', '.join(x) if isinstance(x, list) else str(x)
                    )
                    export_df['resources'] = export_df['resources'].apply(
                        lambda x: ', '.join(x) if isinstance(x, list) else str(x)
                    )
                    
                    csv_data = export_df.to_csv(index=False)
                    st.download_button(
                        label="📄 Download Data (CSV)",
                        data=csv_data,
                        file_name=f"project_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
    
    else:
        st.info("📝 No project data available for Gantt chart")
        st.markdown("""
        **To create a Gantt chart:**
        1. Add project activities using the Activity Viewer
        2. Include start dates, end dates, and dependencies
        3. Return here to visualize your project timeline
        
        **Sample data is available if using session database mode.**
        """)

if __name__ == "__main__":
    # Show sidebar
    Navbar()
    
    # Set page configuration
    st.set_page_config(
        page_title="Project Gantt Chart",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    # Initialize database
    engine = init_database()
        
    # Run the gantt chart page
    gantt_chart_page(engine)
