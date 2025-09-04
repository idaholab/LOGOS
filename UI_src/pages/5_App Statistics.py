# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 14:31:07 2025

@author: ChEdw
"""
# Default libraries
from datetime import datetime, date
import json
import time

# Installed libraries
import streamlit as st
import plotly.express as px
from sqlalchemy import text

# Intrinsic packages
from lib.db import init_database, get_database_stats, load_data_from_db
from lib.Navbar import Navbar

def format_list_for_display(lst):
    """Format list for display with better handling"""
    if isinstance(lst, list) and lst:
        return ', '.join(str(item) for item in lst)
    elif isinstance(lst, str) and lst:
        return lst
    return 'None'

def add_new_activity(engine, activity_data):
    """Add new activity to database with validation"""
    if engine is None:
        return False, "No database connection"
    
    try:
        # Validate date range
        if activity_data['start_date'] > activity_data['end_date']:
            return False, "End date must be after start date"
        
        insert_query = """
        INSERT INTO project_activities 
        (activity_name, description, start_date, end_date, resources, dependencies, critical_path, group_name)
        VALUES (:activity_name, :description, :start_date, :end_date, :resources, :dependencies, :critical_path, :group_name)
        """
        
        with engine.connect() as conn:
            conn.execute(text(insert_query), activity_data)
            conn.commit()
        
        return True, "Activity added successfully!"
    
    except Exception as e:
        return False, f"Error adding activity: {str(e)}"
    


if __name__ == "__main__":
    Navbar()
    
    engine = init_database()
    
    # Database connection status with backup info
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        st.success("✅ Connected to PostgreSQL Database")
        stats = get_database_stats(engine)
    elif st.session_state.get('database_type') == 'session':
        st.warning("🔄 Using Temporary Session Database")
        st.info("💡 Session data will persist during this browser session only")
        stats = get_database_stats(None)
    else:
        st.error("❌ No database connection available")
        stats = {}

    # Display database statistics if available
    if stats:
        st.markdown("### Database Overview")
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Activities", stats.get('total_activities', 0))
        with col2:
            st.metric("Critical Path", stats.get('critical_activities', 0))
        with col3:
            st.metric("Unique Groups", stats.get('unique_groups', 0))
        with col4:
            if st.session_state.get('database_type') == 'postgresql':
                completion_rate = 0
                if stats.get('total_activities', 0) > 0:
                    completion_rate = round((stats.get('completed', 0) / stats.get('total_activities', 0)) * 100, 1)
                st.metric("Completion Rate", f"{completion_rate}%")
            else:
                st.metric("Database Type", "Session")
                
    # Main content area
    if engine is not None:
        # Get and display statistics
        stats = get_database_stats(engine)
        
        if stats:
            st.subheader("📈 Project Overview")
            
            # Create metrics in columns
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric(
                    "Total Activities", 
                    stats.get('total_activities', 0),
                    help="Total number of project activities"
                )
            
            with col2:
                st.metric(
                    "Critical Path", 
                    stats.get('critical_activities', 0),
                    help="Activities on the critical path"
                )
            
            with col3:
                st.metric(
                    "Active Groups", 
                    stats.get('unique_groups', 0),
                    help="Number of different teams/groups"
                )
            
            with col4:
                completion_rate = 0
                if stats.get('total_activities', 0) > 0:
                    completion_rate = round((stats.get('completed', 0) / stats.get('total_activities', 0)) * 100, 1)
                st.metric(
                    "Completion Rate", 
                    f"{completion_rate}%",
                    help="Percentage of completed activities"
                )
            
            # Status breakdown
            st.subheader("📊 Activity Status Distribution")
            status_col1, status_col2, status_col3 = st.columns(3)
            
            with status_col1:
                st.metric("Not Started", stats.get('not_started', 0), help="Future activities")
            with status_col2:
                st.metric("In Progress", stats.get('in_progress', 0), help="Currently active")
            with status_col3:
                st.metric("Completed", stats.get('completed', 0), help="Finished activities")
        
        st.markdown("---")
        
        # Load and display data
        df = load_data_from_db(engine)
        
        if not df.empty:
            # Sidebar filters
            with st.sidebar:
                st.subheader("🔍 Filters")
                
                # Group filter
                groups = ['All'] + sorted(df['Group'].dropna().unique().tolist())
                selected_group = st.selectbox("Filter by Group", groups)
                
                # Critical path filter
                critical_filter = st.selectbox("Critical Path", ['All', 'Yes', 'No'])
                
                # Status filter
                statuses = ['All'] + sorted(df['Status'].unique().tolist())
                selected_status = st.selectbox("Filter by Status", statuses)
                
                # Date range filter
                st.subheader("📅 Date Range")
                min_date = df['Start Date'].min().date()
                max_date = df['End Date'].max().date()
                
                date_range = st.date_input(
                    "Select date range",
                    value=(min_date, max_date),
                    min_value=min_date,
                    max_value=max_date
                )
            
            # Apply filters
            filtered_df = df.copy()
            
            if selected_group != 'All':
                filtered_df = filtered_df[filtered_df['Group'] == selected_group]
            
            if critical_filter == 'Yes':
                filtered_df = filtered_df[filtered_df['Critical Path'] == True]
            elif critical_filter == 'No':
                filtered_df = filtered_df[filtered_df['Critical Path'] == False]
            
            if selected_status != 'All':
                filtered_df = filtered_df[filtered_df['Status'] == selected_status]
            
            if len(date_range) == 2:
                start_date, end_date = date_range
                filtered_df = filtered_df[
                    (filtered_df['Start Date'].dt.date >= start_date) & 
                    (filtered_df['End Date'].dt.date <= end_date)
                ]
            
            # Main content tabs
            tab1, tab2, tab3, tab4 = st.tabs(["📋 Activities Table", "📊 Analytics", "➕ Add Activity", "📥 Export Data"])
            
            with tab1:
                st.subheader("Project Activities")
                st.info(f"Showing {len(filtered_df)} of {len(df)} activities")
                
                # Format data for display
                display_df = filtered_df.copy()
                display_df['Resources'] = display_df['Resources'].apply(format_list_for_display)
                display_df['Dependencies'] = display_df['Dependencies'].apply(format_list_for_display)
                display_df['Critical Path'] = display_df['Critical Path'].map({True: '✅ Yes', False: '❌ No'})
                display_df['Start Date'] = display_df['Start Date'].dt.strftime('%Y-%m-%d')
                display_df['End Date'] = display_df['End Date'].dt.strftime('%Y-%m-%d')
                
                # Display table with better formatting
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Activity Name": st.column_config.TextColumn("Activity Name", width="medium"),
                        "Description": st.column_config.TextColumn("Description", width="large"),
                        "Status": st.column_config.TextColumn("Status", width="small"),
                        "Duration (Days)": st.column_config.NumberColumn("Duration", format="%d days"),
                    }
                )
            
            with tab2:
                st.subheader("📊 Project Analytics")
                
                # Create visualizations
                viz_col1, viz_col2 = st.columns(2)
                
                with viz_col1:
                    # Activities by group
                    group_counts = filtered_df['Group'].value_counts()
                    if not group_counts.empty:
                        fig = px.pie(
                            values=group_counts.values,
                            names=group_counts.index,
                            title="Activities by Group"
                        )
                        st.plotly_chart(fig, use_container_width=True)
                
                with viz_col2:
                    # Status distribution
                    status_counts = filtered_df['Status'].value_counts()
                    if not status_counts.empty:
                        fig = px.bar(
                            x=status_counts.index,
                            y=status_counts.values,
                            title="Activities by Status",
                            color=status_counts.values,
                            color_continuous_scale="viridis"
                        )
                        fig.update_layout(showlegend=False)
                        st.plotly_chart(fig, use_container_width=True)
                
                # Timeline visualization
                st.subheader("📅 Project Timeline")
                if not filtered_df.empty:
                    fig = px.timeline(
                        filtered_df,
                        x_start="Start Date",
                        x_end="End Date", 
                        y="Activity Name",
                        color="Group",
                        title="Project Activity Timeline",
                        height=max(400, len(filtered_df) * 25)
                    )
                    fig.update_layout(
                        xaxis_title="Date",
                        yaxis_title="Activities"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                
                # Critical path analysis
                st.subheader("⚡ Critical Path Analysis")
                critical_df = filtered_df[filtered_df['Critical Path'] == True]
                non_critical_df = filtered_df[filtered_df['Critical Path'] == False]
                
                crit_col1, crit_col2 = st.columns(2)
                with crit_col1:
                    st.metric("Critical Activities", len(critical_df))
                    if not critical_df.empty:
                        st.write("**Critical Path Activities:**")
                        for activity in critical_df['Activity Name'].tolist():
                            st.write(f"• {activity}")
                
                with crit_col2:
                    st.metric("Non-Critical Activities", len(non_critical_df))
                    avg_duration = filtered_df['Duration (Days)'].mean()
                    st.metric("Average Duration", f"{avg_duration:.1f} days")
            
            with tab3:
                st.subheader("➕ Add New Activity")
                
                with st.form("add_activity", clear_on_submit=True):
                    form_col1, form_col2 = st.columns(2)
                    
                    with form_col1:
                        activity_name = st.text_input("Activity Name *", help="Enter a unique activity name")
                        description = st.text_area("Description", help="Detailed description of the activity")
                        start_date = st.date_input("Start Date *", value=date.today())
                        end_date = st.date_input("End Date *", value=date.today())
                    
                    with form_col2:
                        resources = st.text_input(
                            "Resources (comma-separated)", 
                            help="e.g., Developer, Server, Database",
                            placeholder="Resource1, Resource2, Resource3"
                        )
                        dependencies = st.text_input(
                            "Dependencies (comma-separated)", 
                            help="Activities that must complete before this one",
                            placeholder="Activity1, Activity2"
                        )
                        critical_path = st.checkbox("Critical Path", help="Is this activity on the critical path?")
                        
                        # Dynamic group selection
                        existing_groups = sorted(df['Group'].unique().tolist())
                        group_option = st.selectbox("Group Selection", ["Select existing", "Create new"])
                        
                        if group_option == "Select existing":
                            group_name = st.selectbox("Select Group *", existing_groups)
                        else:
                            group_name = st.text_input("New Group Name *", help="Enter a new group name")
                    
                    submitted = st.form_submit_button("Add Activity", use_container_width=True, type="primary")
                    
                    if submitted:
                        if activity_name and start_date and end_date and group_name:
                            # Parse comma-separated lists
                            resources_list = [r.strip() for r in resources.split(',') if r.strip()] if resources else []
                            dependencies_list = [d.strip() for d in dependencies.split(',') if d.strip()] if dependencies else []
                            
                            activity_data = {
                                'activity_name': activity_name,
                                'description': description or '',
                                'start_date': start_date,
                                'end_date': end_date,
                                'resources': json.dumps(resources_list),
                                'dependencies': json.dumps(dependencies_list),
                                'critical_path': critical_path,
                                'group_name': group_name
                            }
                            
                            success, message = add_new_activity(engine, activity_data)
                            if success:
                                st.success(message)
                                st.balloons()
                                # Clear cache to refresh data
                                st.cache_data.clear()
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(message)
                        else:
                            st.error("Please fill in all required fields marked with *")
            
            with tab4:
                st.subheader("📥 Export Data")
                
                export_col1, export_col2 = st.columns(2)
                
                with export_col1:
                    st.write("**Export Options:**")
                    
                    # Format data for export
                    export_df = filtered_df.copy()
                    export_df['Resources'] = export_df['Resources'].apply(format_list_for_display)
                    export_df['Dependencies'] = export_df['Dependencies'].apply(format_list_for_display)
                    export_df['Start Date'] = export_df['Start Date'].dt.strftime('%Y-%m-%d')
                    export_df['End Date'] = export_df['End Date'].dt.strftime('%Y-%m-%d')
                    
                    # CSV export
                    csv_data = export_df.to_csv(index=False)
                    st.download_button(
                        label="📄 Download as CSV",
                        data=csv_data,
                        file_name=f"project_activities_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                    
                    # JSON export
                    json_data = export_df.to_json(orient='records', date_format='iso', indent=2)
                    st.download_button(
                        label="📋 Download as JSON",
                        data=json_data,
                        file_name=f"project_activities_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json",
                        use_container_width=True
                    )
                
                with export_col2:
                    st.write("**Export Summary:**")
                    st.info(f"📊 Total Records: {len(export_df)}")
                    st.info(f"📅 Date Range: {export_df['Start Date'].min()} to {export_df['End Date'].max()}")
                    st.info(f"👥 Groups: {', '.join(export_df['Group'].unique())}")
                    
                    # Preview
                    st.write("**Data Preview:**")
                    st.dataframe(export_df.head(3), use_container_width=True)
        
        else:
            st.warning("No data available in the database.")
            st.info("The database connection is working, but no activities were found. This might be due to:")
            st.markdown("""
            - Database initialization is still in progress
            - Sample data was not loaded properly
            - Database is empty
            
            **Try refreshing the page or check the database logs.**
            """)
    
    else:
        st.error("❌ Cannot connect to database")
        st.markdown("""
        **Troubleshooting Steps:**
        1. Ensure Docker containers are running: `docker-compose ps`
        2. Check database logs: `docker-compose logs postgres`
        3. Verify environment variables in `.env` file
        4. Try restarting containers: `docker-compose restart`
        """)    