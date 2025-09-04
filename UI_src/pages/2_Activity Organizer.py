# -*- coding: utf-8 -*-
"""
Created on Wed Aug 27 16:36:11 2025

@author: ChEdw
"""
# Default libraries
from datetime import datetime
import json

# Installed libraries
import streamlit as st
import pandas as pd
from sqlalchemy import text
import ast

# Intrinsic packages
from lib.db import init_database
from lib.Navbar import Navbar

def init_session_database():
    """Initialize temporary session database with sample data"""
    if 'temp_database' not in st.session_state:
        # Create sample data
        sample_data = [
            {
                'id': 1,
                'activity_name': 'Project Planning',
                'description': 'Initial project planning and requirement gathering',
                'start_date': datetime(2024, 1, 1),
                'end_date': datetime(2024, 1, 15),
                'resources': ['Project Manager', 'Business Analyst', 'Meeting Room'],
                'dependencies': [],
                'critical_path': True,
                'group_name': 'Management'
            },
            {
                'id': 2,
                'activity_name': 'Database Design',
                'description': 'Design database schema and relationships',
                'start_date': datetime(2024, 1, 16),
                'end_date': datetime(2024, 1, 30),
                'resources': ['Database Architect', 'Senior Developer', 'Design Tools'],
                'dependencies': ['Project Planning'],
                'critical_path': True,
                'group_name': 'Backend Team'
            },
            {
                'id': 3,
                'activity_name': 'Frontend Development',
                'description': 'Develop user interface and user experience',
                'start_date': datetime(2024, 2, 1),
                'end_date': datetime(2024, 3, 15),
                'resources': ['Frontend Developer', 'UI/UX Designer', 'Development Environment'],
                'dependencies': ['Database Design'],
                'critical_path': False,
                'group_name': 'Frontend Team'
            },
            {
                'id': 4,
                'activity_name': 'Testing Phase',
                'description': 'Comprehensive testing of application functionality',
                'start_date': datetime(2024, 3, 16),
                'end_date': datetime(2024, 3, 30),
                'resources': ['QA Engineer', 'Test Environment', 'Automation Tools'],
                'dependencies': ['Frontend Development'],
                'critical_path': True,
                'group_name': 'QA Team'
            },
            {
                'id': 5,
                'activity_name': 'Deployment',
                'description': 'Deploy application to production environment',
                'start_date': datetime(2024, 4, 1),
                'end_date': datetime(2024, 4, 15),
                'resources': ['DevOps Engineer', 'Production Servers', 'Monitoring Tools'],
                'dependencies': ['Testing Phase'],
                'critical_path': True,
                'group_name': 'DevOps Team'
            }
        ]
        
        # Convert to DataFrame for easy manipulation
        st.session_state.temp_database = pd.DataFrame(sample_data)
        st.session_state.database_type = 'session'
        
def add_new_activity(engine, activity_data):
    """Add new activity to database (PostgreSQL or session)"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            insert_query = """
            INSERT INTO project_activities 
            (activity_name, description, start_date, end_date, resources, dependencies, critical_path, group_name)
            VALUES (:activity_name, :description, :start_date, :end_date, :resources, :dependencies, :critical_path, :group_name)
            """
            
            with engine.connect() as conn:
                conn.execute(text(insert_query), activity_data)
                conn.commit()
            
            return True, "Activity added to PostgreSQL database successfully!"
        
        except Exception as e:
            return False, f"Error adding to PostgreSQL: {str(e)}"
    
    elif st.session_state.get('database_type') == 'session':
        try:
            # Add to session database
            if 'temp_database' in st.session_state:
                new_id = len(st.session_state.temp_database) + 1
                new_row = {
                    'id': new_id,
                    'activity_name': activity_data['activity_name'],
                    'description': activity_data['description'],
                    'start_date': activity_data['start_date'],
                    'end_date': activity_data['end_date'],
                    'resources': json.loads(activity_data['resources']),
                    'dependencies': json.loads(activity_data['dependencies']),
                    'critical_path': activity_data['critical_path'],
                    'group_name': activity_data['group_name']
                }
                
                # Add new row to DataFrame
                new_df = pd.DataFrame([new_row])
                st.session_state.temp_database = pd.concat([st.session_state.temp_database, new_df], ignore_index=True)
                
                return True, "Activity added to session database successfully!"
            else:
                return False, "Session database not initialized"
        
        except Exception as e:
            return False, f"Error adding to session database: {str(e)}"
    
    else:
        return False, "No database connection available"

def update_activity(engine, activity_id, activity_data):
    """Update existing activity in database (PostgreSQL or session)"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            update_query = """
            UPDATE project_activities 
            SET activity_name = :activity_name,
                description = :description,
                start_date = :start_date,
                end_date = :end_date,
                resources = :resources,
                dependencies = :dependencies,
                critical_path = :critical_path,
                group_name = :group_name
            WHERE id = :id
            """
            
            activity_data['id'] = activity_id
            
            with engine.connect() as conn:
                result = conn.execute(text(update_query), activity_data)
                conn.commit()
                
                if result.rowcount > 0:
                    return True, "Activity updated in PostgreSQL database successfully!"
                else:
                    return False, "Activity not found in database"
        
        except Exception as e:
            return False, f"Error updating PostgreSQL: {str(e)}"
    
    elif st.session_state.get('database_type') == 'session':
        try:
            # Update in session database
            if 'temp_database' in st.session_state:
                df = st.session_state.temp_database
                
                # Find the row to update
                mask = df['id'] == activity_id
                if mask.any():
                    # Update the row
                    df.loc[mask, 'activity_name'] = activity_data['activity_name']
                    df.loc[mask, 'description'] = activity_data['description']
                    df.loc[mask, 'start_date'] = activity_data['start_date']
                    df.loc[mask, 'end_date'] = activity_data['end_date']
                    df.loc[mask, 'resources'] = [json.loads(activity_data['resources'])]
                    df.loc[mask, 'dependencies'] = [json.loads(activity_data['dependencies'])]
                    df.loc[mask, 'critical_path'] = activity_data['critical_path']
                    df.loc[mask, 'group_name'] = activity_data['group_name']
                    
                    st.session_state.temp_database = df
                    return True, "Activity updated in session database successfully!"
                else:
                    return False, "Activity not found in session database"
            else:
                return False, "Session database not initialized"
        
        except Exception as e:
            return False, f"Error updating session database: {str(e)}"
    
    else:
        return False, "No database connection available"

def delete_activity(engine, activity_id):
    """Delete activity from database (PostgreSQL or session)"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            delete_query = "DELETE FROM project_activities WHERE id = :id"
            
            with engine.connect() as conn:
                result = conn.execute(text(delete_query), {'id': activity_id})
                conn.commit()
                
                if result.rowcount > 0:
                    return True, "Activity deleted from PostgreSQL database successfully!"
                else:
                    return False, "Activity not found in database"
        
        except Exception as e:
            return False, f"Error deleting from PostgreSQL: {str(e)}"
    
    elif st.session_state.get('database_type') == 'session':
        try:
            # Delete from session database
            if 'temp_database' in st.session_state:
                df = st.session_state.temp_database
                
                # Find and remove the row
                mask = df['id'] != activity_id
                if len(df[~mask]) > 0:  # Check if row exists
                    st.session_state.temp_database = df[mask].reset_index(drop=True)
                    return True, "Activity deleted from session database successfully!"
                else:
                    return False, "Activity not found in session database"
            else:
                return False, "Session database not initialized"
        
        except Exception as e:
            return False, f"Error deleting from session database: {str(e)}"
    
    else:
        return False, "No database connection available"

def get_activity_by_id(engine, activity_id):
    """Get specific activity by ID for editing"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            query = "SELECT * FROM project_activities WHERE id = :id"
            
            with engine.connect() as conn:
                result = conn.execute(text(query), {'id': activity_id})
                row = result.fetchone()
                
                if row:
                    return dict(row._mapping)
                else:
                    return None
        
        except Exception as e:
            st.error(f"Error fetching activity: {str(e)}")
            return None
    
    elif st.session_state.get('database_type') == 'session':
        try:
            if 'temp_database' in st.session_state:
                df = st.session_state.temp_database
                mask = df['id'] == activity_id
                
                if mask.any():
                    row = df[mask].iloc[0]
                    return row.to_dict()
                else:
                    return None
            else:
                return None
        
        except Exception as e:
            st.error(f"Error fetching activity: {str(e)}")
            return None
    else:
        return None

def load_database_data_with_ids(engine):    
    """Load all activities from database with IDs for editing"""
    if engine is not None and st.session_state.get('database_type') == 'postgresql':
        try:
            query = """
            SELECT 
                id,
                activity_name as "Activity Name",
                description as "Description", 
                start_date as "Start Date",
                end_date as "End Date",
                resources as "Resources",
                dependencies as "Dependencies", 
                critical_path as "Critical Path",
                group_name as "Group"
            FROM project_activities 
            ORDER BY start_date, activity_name;
            """
            
            df = pd.read_sql(query, engine)
            
            # Convert JSONB columns back to lists for display
            if not df.empty:
                df['Resources'] = df['Resources'].apply(lambda x: x if isinstance(x, list) else [])
                df['Dependencies'] = df['Dependencies'].apply(lambda x: x if isinstance(x, list) else [])
            
            return df, "postgresql"
            
        except Exception as e:
            st.error(f"Error loading from PostgreSQL: {str(e)}")
            return pd.DataFrame(), "error"
    
    elif st.session_state.get('database_type') == 'session':
        # Load from session state backup database
        if 'temp_database' in st.session_state:
            df = st.session_state.temp_database.copy()
            
            # Rename columns to match display format but keep ID
            column_mapping = {
                'activity_name': 'Activity Name',
                'description': 'Description',
                'start_date': 'Start Date',
                'end_date': 'End Date',
                'resources': 'Resources',
                'dependencies': 'Dependencies',
                'critical_path': 'Critical Path',
                'group_name': 'Group'
            }
            
            df = df.rename(columns=column_mapping)
            
            return df, "session"
        
        return pd.DataFrame(), "session_empty"
    
    return pd.DataFrame(), "no_connection"

def transfer_uploaded_data_to_database(engine, uploaded_df, mode="append"):
   """Transfer uploaded data to the database (PostgreSQL or session) with append/overwrite options"""
   if engine is not None and st.session_state.get('database_type') == 'postgresql':
       try:
           transfer_count = 0
           errors = []
           
           # Handle overwrite mode - clear existing data
           if mode == "overwrite":
               with engine.connect() as conn:
                   delete_query = "DELETE FROM project_activities"
                   conn.execute(text(delete_query))
                   conn.commit()
               st.info("🔄 Existing database data cleared for overwrite")
           
           for index, row in uploaded_df.iterrows():
               try:
                   # Prepare activity data
                   activity_data = {
                       'activity_name': str(row.get('Activity Name', f'Activity_{index+1}')),
                       'description': str(row.get('Description', '')),
                       'start_date': pd.to_datetime(row.get('Start Date')).date() if pd.notna(row.get('Start Date')) else datetime.now().date(),
                       'end_date': pd.to_datetime(row.get('End Date')).date() if pd.notna(row.get('End Date')) else datetime.now().date(),
                       'resources': json.dumps(row.get('Resources', []) if isinstance(row.get('Resources'), list) else []),
                       'dependencies': json.dumps(row.get('Dependencies', []) if isinstance(row.get('Dependencies'), list) else []),
                       'critical_path': bool(row.get('Critical Path', False)),
                       'group_name': str(row.get('Group', 'Default Group'))
                   }
                   
                   # Insert into database
                   insert_query = """
                   INSERT INTO project_activities 
                   (activity_name, description, start_date, end_date, resources, dependencies, critical_path, group_name)
                   VALUES (:activity_name, :description, :start_date, :end_date, :resources, :dependencies, :critical_path, :group_name)
                   """
                   
                   with engine.connect() as conn:
                       conn.execute(text(insert_query), activity_data)
                       conn.commit()
                   
                   transfer_count += 1
                   
               except Exception as e:
                   errors.append(f"Row {index+1}: {str(e)}")
           
           if errors:
               return transfer_count, f"Transferred {transfer_count} activities ({mode} mode). Errors: {'; '.join(errors[:3])}"
           else:
               return transfer_count, f"Successfully transferred all {transfer_count} activities to PostgreSQL database ({mode} mode)!"
               
       except Exception as e:
           return 0, f"Error transferring to PostgreSQL ({mode} mode): {str(e)}"
   
   elif st.session_state.get('database_type') == 'session':
       try:
           transfer_count = 0
           
           # Initialize session database if not exists
           if 'temp_database' not in st.session_state:
               st.session_state.temp_database = pd.DataFrame()
           
           # Handle overwrite mode - clear existing data
           if mode == "overwrite":
               st.session_state.temp_database = pd.DataFrame()
               current_max_id = 0
               st.info("🔄 Existing session data cleared for overwrite")
           else:
               current_max_id = 0
               if not st.session_state.temp_database.empty:
                   current_max_id = st.session_state.temp_database['id'].max()
           
           new_rows = []
           
           for index, row in uploaded_df.iterrows():
               try:
                   new_id = current_max_id + index + 1
                   
                   # Handle resources and dependencies
                   resources = row.get('Resources', [])
                   if isinstance(resources, str):
                       resources = [r.strip() for r in resources.split(',') if r.strip()]
                   elif not isinstance(resources, list):
                       resources = []
                   
                   dependencies = row.get('Dependencies', [])
                   if isinstance(dependencies, str):
                       dependencies = [d.strip() for d in dependencies.split(',') if d.strip()]
                   elif not isinstance(dependencies, list):
                       dependencies = []
                   
                   new_row = {
                       'id': new_id,
                       'activity_name': str(row.get('Activity Name', f'Activity_{index+1}')),
                       'description': str(row.get('Description', '')),
                       'start_date': pd.to_datetime(row.get('Start Date')).to_pydatetime() if pd.notna(row.get('Start Date')) else datetime.now(),
                       'end_date': pd.to_datetime(row.get('End Date')).to_pydatetime() if pd.notna(row.get('End Date')) else datetime.now(),
                       'resources': resources,
                       'dependencies': dependencies,
                       'critical_path': bool(row.get('Critical Path', False)),
                       'group_name': str(row.get('Group', 'Default Group'))
                   }
                   
                   new_rows.append(new_row)
                   transfer_count += 1
                   
               except Exception as e:
                   continue  # Skip problematic rows
           
           # Add new rows to session database
           if new_rows:
               new_df = pd.DataFrame(new_rows)
               if st.session_state.temp_database.empty:
                   st.session_state.temp_database = new_df
               else:
                   st.session_state.temp_database = pd.concat([st.session_state.temp_database, new_df], ignore_index=True)
           
           return transfer_count, f"Successfully transferred {transfer_count} activities to session database ({mode} mode)!"
           
       except Exception as e:
           return 0, f"Error transferring to session database ({mode} mode): {str(e)}"
   
   else:
       return 0, "No database connection available for transfer"

def parse_uploaded_file(uploaded_file):
    """Parse uploaded CSV or JSON file"""
    try:
        file_extension = uploaded_file.name.split('.')[-1].lower()
        
        if file_extension == 'csv':
            # Read CSV file
            df = pd.read_csv(uploaded_file)
            return df, "csv"
            
        elif file_extension == 'json':
            # Read JSON file
            json_data = json.load(uploaded_file)
            
            # Handle different JSON structures
            if isinstance(json_data, list):
                df = pd.DataFrame(json_data)
            elif isinstance(json_data, dict):
                # If it's a dict, try to convert to DataFrame
                if 'data' in json_data:
                    df = pd.DataFrame(json_data['data'])
                else:
                    # Assume the dict itself is the data
                    df = pd.DataFrame([json_data])
            else:
                st.error("Unsupported JSON structure")
                return pd.DataFrame(), "error"
                
            return df, "json"
        
        else:
            st.error("Unsupported file format. Please upload CSV or JSON files only.")
            return pd.DataFrame(), "error"
            
    except Exception as e:
        st.error(f"Error parsing file: {str(e)}")
        return pd.DataFrame(), "error"

def format_dataframe_for_display(df):
    """Format DataFrame for better display"""
    if df.empty:
        return df
    
    display_df = df.copy()
    
    # Format list columns if they exist
    list_columns = ['Resources', 'Dependencies']
    for col in list_columns:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: ', '.join(map(str, x)) if isinstance(x, list) and x 
                else ', '.join(map(str, ast.literal_eval(x))) if isinstance(x, str) and x.startswith('[') 
                else str(x) if x else 'None'
            )
    
    # Format boolean columns
    if 'Critical Path' in display_df.columns:
        display_df['Critical Path'] = display_df['Critical Path'].map({
            True: '✅ Yes', 
            False: '❌ No',
            'true': '✅ Yes',
            'false': '❌ No',
            1: '✅ Yes',
            0: '❌ No'
        }).fillna(display_df['Critical Path'])
    
    # Format date columns
    date_columns = ['Start Date', 'End Date']
    for col in date_columns:
        if col in display_df.columns:
            try:
                display_df[col] = pd.to_datetime(display_df[col]).dt.strftime('%Y-%m-%d')
            except:
                pass  # Keep original format if conversion fails
    
    return display_df

def validate_data_structure(df):
    """Validate if uploaded data has expected structure"""
    expected_columns = ['Activity Name', 'Description', 'Start Date', 'End Date', 
                       'Resources', 'Dependencies', 'Critical Path', 'Group']
    
    missing_columns = []
    for col in expected_columns:
        if col not in df.columns:
            # Check for alternative column names
            alternatives = {
                'Activity Name': ['activity_name', 'name', 'activity'],
                'Description': ['description', 'desc'],
                'Start Date': ['start_date', 'start', 'begin_date'],
                'End Date': ['end_date', 'end', 'finish_date'],
                'Resources': ['resources', 'resource'],
                'Dependencies': ['dependencies', 'deps', 'dependency'],
                'Critical Path': ['critical_path', 'critical', 'is_critical'],
                'Group': ['group_name', 'group', 'team']
            }
            
            found_alternative = False
            for alt in alternatives.get(col, []):
                if alt in df.columns:
                    df = df.rename(columns={alt: col})
                    found_alternative = True
                    break
            
            if not found_alternative:
                missing_columns.append(col)
    
    return df, missing_columns

# Main Activity Viewer Page
def activity_viewer_page(engine):
    """Main function for the Activity Viewer page"""
    
    st.title("📋 Activity Viewer")
    st.markdown("View, add, edit activities from the database or upload your own data files")
    
    # Create tabs for different functionalities
    tab1, tab2, tab3, tab4 = st.tabs(["🗄️ Database Data", "📁 Upload Data", "➕ Add Activity", "✏️ Edit Activity"])
    
    with tab1:
        st.subheader("Database Activities")
        
        # Add refresh and delete database buttons
        refresh_col1, refresh_col2, refresh_col3 = st.columns([2, 1, 1])
        with refresh_col2:
            if st.button("🔄 Refresh Database", help="Reload data from database"):
                # Clear any cached data
                if hasattr(st, 'cache_data'):
                    st.cache_data.clear()
                if hasattr(st, 'cache_resource'):
                    st.cache_resource.clear()
                st.rerun()
        
        with refresh_col3:
            if st.button("🗑️ Clear Database", type="secondary", help="Delete all records from database"):
                if st.session_state.get('confirm_clear_database') != True:
                    st.session_state.confirm_clear_database = True
                    st.warning("⚠️ **WARNING**: This will delete ALL database records! Click the button again to confirm.")
                    st.stop()
                else:
                    # Clear database based on type
                    success = False
                    if engine is not None and st.session_state.get('database_type') == 'postgresql':
                        try:
                            with engine.connect() as conn:
                                conn.execute(text("DELETE FROM project_activities"))
                                conn.commit()
                            success = True
                            message = "All PostgreSQL database records deleted successfully!"
                        except Exception as e:
                            message = f"Error clearing PostgreSQL database: {str(e)}"
                    elif st.session_state.get('database_type') == 'session':
                        try:
                            st.session_state.temp_database = pd.DataFrame()
                            success = True
                            message = "All session database records deleted successfully!"
                        except Exception as e:
                            message = f"Error clearing session database: {str(e)}"
                    else:
                        message = "No database connection available"
                    
                    # Clear confirmation state
                    if 'confirm_clear_database' in st.session_state:
                        del st.session_state.confirm_clear_database
                    
                    if success:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
                
        # Load database data with IDs for editing
        df, data_source = load_database_data_with_ids(engine)
        
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
            # Display data summary
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Activities", len(df))
            with col2:
                if 'Critical Path' in df.columns:
                    critical_count = len(df[df['Critical Path'] == True])
                    st.metric("Critical Path", critical_count)
                else:
                    st.metric("Critical Path", "N/A")
            with col3:
                if 'Group' in df.columns:
                    unique_groups = df['Group'].nunique()
                    st.metric("Unique Groups", unique_groups)
                else:
                    st.metric("Unique Groups", "N/A")
            
            st.markdown("---")
            
            # Filter options
            st.subheader("🔍 Filters")
            filter_col1, filter_col2 = st.columns(2)
            
            with filter_col1:
                if 'Group' in df.columns:
                    groups = ['All'] + sorted(df['Group'].dropna().unique().tolist())
                    selected_group = st.selectbox("Filter by Group", groups, key="db_group_filter")
                else:
                    selected_group = 'All'
            
            with filter_col2:
                if 'Critical Path' in df.columns:
                    critical_options = ['All', 'Critical Only', 'Non-Critical Only']
                    critical_filter = st.selectbox("Critical Path Filter", critical_options, key="db_critical_filter")
                else:
                    critical_filter = 'All'
            
            # Apply filters
            filtered_df = df.copy()
            
            if selected_group != 'All' and 'Group' in df.columns:
                filtered_df = filtered_df[filtered_df['Group'] == selected_group]
            
            if critical_filter != 'All' and 'Critical Path' in df.columns:
                if critical_filter == 'Critical Only':
                    filtered_df = filtered_df[filtered_df['Critical Path'] == True]
                elif critical_filter == 'Non-Critical Only':
                    filtered_df = filtered_df[filtered_df['Critical Path'] == False]
            
            # Display filtered data with action buttons
            st.subheader(f"📊 Activities Table ({len(filtered_df)} records)")
            
            if not filtered_df.empty:
                # Create display dataframe without ID for user view
                display_df = format_dataframe_for_display(filtered_df.drop('id', axis=1) if 'id' in filtered_df.columns else filtered_df)
                
                # Show the data table
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True
                )
                
                # Action buttons section
                st.subheader("🔧 Actions")
                action_col1, action_col2 = st.columns(2)
                
                with action_col1:
                    st.markdown("**Edit Activity:**")
                    if 'id' in filtered_df.columns:
                        activity_options = []
                        for _, row in filtered_df.iterrows():
                            activity_options.append(f"ID {row['id']}: {row['Activity Name']}")
                        
                        selected_activity = st.selectbox("Select activity to edit:", ["Select an activity..."] + activity_options)
                        
                        if selected_activity != "Select an activity...":
                            activity_id = int(selected_activity.split(":")[0].replace("ID ", ""))
                            if st.button("✏️ Edit Selected Activity"):
                                st.session_state.edit_activity_id = activity_id
                                st.session_state.show_edit_form = True
                                st.rerun()
                
                with action_col2:
                    st.markdown("**Delete Activity:**")
                    if 'id' in filtered_df.columns:
                        delete_activity_options = []
                        for _, row in filtered_df.iterrows():
                            delete_activity_options.append(f"ID {row['id']}: {row['Activity Name']}")
                        
                        selected_delete_activity = st.selectbox("Select activity to delete:", ["Select an activity..."] + delete_activity_options, key="delete_select")
                        
                        if selected_delete_activity != "Select an activity...":
                            delete_activity_id = int(selected_delete_activity.split(":")[0].replace("ID ", ""))
                            if st.button("🗑️ Delete Selected Activity", type="secondary"):
                                if st.session_state.get('confirm_delete') != delete_activity_id:
                                    st.session_state.confirm_delete = delete_activity_id
                                    st.warning("⚠️ Click delete again to confirm")
                                    st.rerun()
                                else:
                                    success, message = delete_activity(engine, delete_activity_id)
                                    if success:
                                        st.success(message)
                                        del st.session_state.confirm_delete
                                        st.rerun()
                                    else:
                                        st.error(message)
                
                # Export options
                st.subheader("📥 Export Filtered Data")
                export_col1, export_col2 = st.columns(2)
                
                with export_col1:
                    csv_data = display_df.to_csv(index=False)
                    st.download_button(
                        label="📄 Download as CSV",
                        data=csv_data,
                        file_name=f"activities_filtered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                
                with export_col2:
                    json_data = display_df.to_json(orient='records', date_format='iso', indent=2)
                    st.download_button(
                        label="📋 Download as JSON",
                        data=json_data,
                        file_name=f"activities_filtered_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        mime="application/json"
                    )
            else:
                st.info("No activities match the selected filters.")
        
        else:
            st.info("No database data available. Try uploading a file in the 'Upload Data' tab or add new activities.")
    
    with tab2:
        st.subheader("Upload Data File")
        
        # File uploader
        uploaded_file = st.file_uploader(
            "Choose a CSV or JSON file",
            type=['csv', 'json'],
            help="Upload a CSV or JSON file containing activity data"
        )
        
        if uploaded_file is not None:
            # Parse the uploaded file
            uploaded_df, file_type = parse_uploaded_file(uploaded_file)
            
            if not uploaded_df.empty:
                st.success(f"✅ Successfully loaded {file_type.upper()} file: {uploaded_file.name}")
                
                # Validate and fix column structure
                validated_df, missing_columns = validate_data_structure(uploaded_df)
                
                if missing_columns:
                    st.warning(f"⚠️ Missing expected columns: {', '.join(missing_columns)}")
                    st.info("The table will display available columns. Consider adding missing columns for full compatibility.")
                
                # Display file summary
                st.subheader("📊 File Summary")
                summary_col1, summary_col2, summary_col3 = st.columns(3)
                
                with summary_col1:
                    st.metric("Total Records", len(validated_df))
                with summary_col2:
                    st.metric("Total Columns", len(validated_df.columns))
                with summary_col3:
                    st.metric("File Type", file_type.upper())
                
                # Show column information
                st.subheader("📋 Column Information")
                col_info = pd.DataFrame({
                    'Column Name': validated_df.columns,
                    'Data Type': [str(validated_df[col].dtype) for col in validated_df.columns],
                    'Sample Value': [str(validated_df[col].iloc[0]) if len(validated_df) > 0 else 'N/A' for col in validated_df.columns]
                })
                st.dataframe(col_info, use_container_width=True, hide_index=True)
                
                st.markdown("---")
                
                # Filter options for uploaded data
                st.subheader("🔍 Filters")
                upload_filter_col1, upload_filter_col2 = st.columns(2)
                
                with upload_filter_col1:
                    if 'Group' in validated_df.columns:
                        upload_groups = ['All'] + sorted(validated_df['Group'].dropna().unique().tolist())
                        upload_selected_group = st.selectbox("Filter by Group", upload_groups, key="upload_group_filter")
                    else:
                        upload_selected_group = 'All'
                
                with upload_filter_col2:
                    if 'Critical Path' in validated_df.columns:
                        upload_critical_options = ['All', 'Critical Only', 'Non-Critical Only']
                        upload_critical_filter = st.selectbox("Critical Path Filter", upload_critical_options, key="upload_critical_filter")
                    else:
                        upload_critical_filter = 'All'
                
                # Apply filters to uploaded data
                upload_filtered_df = validated_df.copy()
                
                if upload_selected_group != 'All' and 'Group' in validated_df.columns:
                    upload_filtered_df = upload_filtered_df[upload_filtered_df['Group'] == upload_selected_group]
                
                if upload_critical_filter != 'All' and 'Critical Path' in validated_df.columns:
                    if upload_critical_filter == 'Critical Only':
                        upload_filtered_df = upload_filtered_df[upload_filtered_df['Critical Path'] == True]
                    elif upload_critical_filter == 'Non-Critical Only':
                        upload_filtered_df = upload_filtered_df[upload_filtered_df['Critical Path'] == False]
                
                # Display uploaded data
                st.subheader(f"📊 Uploaded Data Table ({len(upload_filtered_df)} records)")
                
                if not upload_filtered_df.empty:
                    display_upload_df = format_dataframe_for_display(upload_filtered_df)
                    st.dataframe(
                        display_upload_df,
                        use_container_width=True,
                        hide_index=True
                    )
                    
                    # Export and transfer options for uploaded data
                    st.subheader("📥 Export and Transfer Options")
                    
                    # Transfer mode selection
                    st.markdown("**🔄 Database Transfer Options:**")
                    transfer_col1, transfer_col2 = st.columns(2)
                    
                    with transfer_col1:
                        transfer_mode = st.radio(
                            "Choose transfer mode:",
                            options=["append", "overwrite"],
                            format_func=lambda x: "📝 Append to existing data" if x == "append" else "🔄 Overwrite existing data",
                            help="Append: Add to existing database data | Overwrite: Replace all existing database data"
                        )
                    
                    with transfer_col2:
                        # Show current database status
                        current_db_count = 0
                        if engine is not None:
                            try:
                                if st.session_state.get('database_type') == 'postgresql':
                                    with engine.connect() as conn:
                                        result = conn.execute(text("SELECT COUNT(*) FROM project_activities")).fetchone()
                                        current_db_count = result[0] if result else 0
                                elif st.session_state.get('database_type') == 'session':
                                    if 'temp_database' in st.session_state:
                                        current_db_count = len(st.session_state.temp_database)
                            except:
                                current_db_count = 0
                        
                        st.info(f"Current database records: **{current_db_count}**")
                        if transfer_mode == "append":
                            estimated_total = current_db_count + len(upload_filtered_df)
                            st.success(f"After append: **{estimated_total}** records")
                        else:
                            st.warning(f"After overwrite: **{len(upload_filtered_df)}** records")
                    
                    # Transfer buttons
                    transfer_button_col1, transfer_button_col2, transfer_button_col3 = st.columns(3)
                    
                    with transfer_button_col1:
                        # Regular transfer button with mode
                        if st.button(
                            f"💾 {'Append to' if transfer_mode == 'append' else 'Overwrite'} Database",
                            type="primary" if transfer_mode == "append" else "secondary",
                            help=f"{'Add uploaded data to existing database records' if transfer_mode == 'append' else 'Replace all existing database records with uploaded data'}"
                        ):
                            # Confirmation for overwrite mode
                            if transfer_mode == "overwrite":
                                if st.session_state.get('confirm_overwrite') != True:
                                    st.session_state.confirm_overwrite = True
                                    st.warning("⚠️ **OVERWRITE MODE**: This will delete all existing database records! Click the button again to confirm.")
                                    st.stop()
                            
                            with st.spinner(f"{'Appending' if transfer_mode == 'append' else 'Overwriting'} database data..."):
                                transfer_count, transfer_message = transfer_uploaded_data_to_database(engine, upload_filtered_df, mode=transfer_mode)
                                
                                # Clear confirmation state
                                if 'confirm_overwrite' in st.session_state:
                                    del st.session_state.confirm_overwrite
                                
                                if transfer_count > 0:
                                    if transfer_mode == "overwrite":
                                        st.success(f"🔄 {transfer_message}")
                                        st.balloons()
                                    else:
                                        st.success(f"📝 {transfer_message}")
                                        st.balloons()
                                    
                                    # Show transfer summary
                                    st.info(f"📊 Transfer Summary ({transfer_mode} mode): {transfer_count} activities processed")
                                    
                                    # Option to refresh database data
                                    if st.button("🔄 View Updated Database", key="view_transferred"):
                                        st.session_state.switch_to_database_tab = True
                                        st.rerun()
                                else:
                                    st.error(transfer_message)
                    
                    with transfer_button_col2:
                        # Export CSV
                        upload_csv_data = display_upload_df.to_csv(index=False)
                        st.download_button(
                            label="📄 Download CSV",
                            data=upload_csv_data,
                            file_name=f"processed_{uploaded_file.name.split('.')[0]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv",
                            help="Download processed data as CSV file"
                        )
                    
                    with transfer_button_col3:
                        # Export JSON
                        upload_json_data = display_upload_df.to_json(orient='records', date_format='iso', indent=2)
                        st.download_button(
                            label="📋 Download JSON",
                            data=upload_json_data,
                            file_name=f"processed_{uploaded_file.name.split('.')[0]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                            mime="application/json",
                            help="Download processed data as JSON file"
                        )
                
                # Transfer confirmation and info
                if st.session_state.get('switch_to_database_tab', False):
                    st.session_state.switch_to_database_tab = False
                    st.info("✅ Data transferred! Switch to the 'Database Data' tab to view all activities.")
                
                    # Enhanced data transfer information
                    st.markdown("---")
                    with st.expander("ℹ️ Transfer Mode Details"):
                        st.markdown("""
                        ### Transfer Modes
                        
                        **📝 Append Mode (Recommended):**
                        - Adds uploaded data to existing database records
                        - Preserves all current data
                        - Safe operation with no data loss
                        - New records get unique IDs
                        
                        **🔄 Overwrite Mode (Use with caution):**
                        - **⚠️ DELETES ALL existing database records**
                        - Replaces entire database with uploaded data
                        - Requires confirmation to prevent accidental data loss
                        - Cannot be undone
                        
                        ### Data Processing:
                        - Validates data format and required fields
                        - Converts data types to match database schema
                        - Handles list fields (Resources, Dependencies)
                        - Assigns default values for missing fields
                        - Provides detailed transfer report
                        
                        ### Database Compatibility:
                        - **PostgreSQL**: Changes saved permanently
                        - **Session Database**: Changes last for current session only
                        
                        ### Safety Features:
                        - Overwrite mode requires double confirmation
                        - Transaction-based operations ensure data integrity
                        - Detailed error reporting for failed records
                        - Current database record count display
                        """)
                else:
                    st.info("No records match the selected filters.")
            else:
                st.error("Failed to load the uploaded file. Please check the file format and try again.")
        
        else:
            st.info("👆 Please upload a CSV or JSON file to view data")
            
            # Show expected data format
            with st.expander("📖 Expected Data Format"):
                st.markdown("""
                **Expected Columns:**
                - **Activity Name** (Text): Name of the activity
                - **Description** (Text): Detailed description
                - **Start Date** (Date): Start date (YYYY-MM-DD format)
                - **End Date** (Date): End date (YYYY-MM-DD format)
                - **Resources** (List): Comma-separated or JSON array
                - **Dependencies** (List): Comma-separated or JSON array
                - **Critical Path** (Boolean): true/false or 1/0
                - **Group** (Text): Group or team name
                
                **Sample CSV Format:**
                ```
                Activity Name,Description,Start Date,End Date,Resources,Dependencies,Critical Path,Group
                Project Planning,Initial planning,2024-01-01,2024-01-15,"Project Manager,Analyst",,true,Management
                Database Design,Design schema,2024-01-16,2024-01-30,"Database Architect","Project Planning",true,Backend Team
                ```
                
                **Sample JSON Format:**
                ```json
                [
                    {
                        "Activity Name": "Project Planning",
                        "Description": "Initial planning",
                        "Start Date": "2024-01-01",
                        "End Date": "2024-01-15",
                        "Resources": ["Project Manager", "Analyst"],
                        "Dependencies": [],
                        "Critical Path": true,
                        "Group": "Management"
                    }
                ]
                ```
                """)
    
    with tab3:
        st.subheader("➕ Add New Activity")
        
        with st.form("add_activity_form", clear_on_submit=True):
            st.markdown("**Fill in the activity details:**")
            
            form_col1, form_col2 = st.columns(2)
            
            with form_col1:
                activity_name = st.text_input("Activity Name *", help="Enter a unique activity name")
                description = st.text_area("Description", help="Detailed description of the activity")
                start_date = st.date_input("Start Date *", value=datetime.now().date())
                end_date = st.date_input("End Date *", value=datetime.now().date())
            
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
                existing_groups = []
                if not df.empty and 'Group' in df.columns:
                   existing_groups = sorted(df['Group'].dropna().unique().tolist())
               
                group_option = st.selectbox("Group Selection", ["Select existing", "Create new"])
               
                if group_option == "Select existing" and existing_groups:
                   group_name = st.selectbox("Select Group *", existing_groups)
                else:
                   group_name = st.text_input("Group Name *", help="Enter a new group name")
           
            submitted = st.form_submit_button("➕ Add Activity", use_container_width=True, type="primary")
           
            if submitted:
               if activity_name and start_date and end_date and group_name:
                   # Validate date range
                   if end_date < start_date:
                       st.error("End date must be after start date")
                   else:
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
                           # Refresh the page to show new data
                           st.rerun()
                       else:
                           st.error(message)
               else:
                   st.error("Please fill in all required fields marked with *")
   
    with tab4:
       st.subheader("✏️ Edit Activity")
       
       # Check if edit form should be shown
       if st.session_state.get('show_edit_form', False) and st.session_state.get('edit_activity_id'):
           activity_id = st.session_state.edit_activity_id
           
           # Load the activity data
           activity_data = get_activity_by_id(engine, activity_id)
           
           if activity_data:
               st.info(f"Editing Activity ID: {activity_id}")
               
               with st.form("edit_activity_form"):
                   st.markdown("**Modify the activity details:**")
                   
                   edit_col1, edit_col2 = st.columns(2)
                   
                   with edit_col1:
                       edit_activity_name = st.text_input("Activity Name *", value=activity_data.get('activity_name', ''))
                       edit_description = st.text_area("Description", value=activity_data.get('description', ''))
                       
                       # Handle date fields
                       current_start = activity_data.get('start_date')
                       if isinstance(current_start, str):
                           current_start = datetime.strptime(current_start, '%Y-%m-%d').date()
                       elif isinstance(current_start, datetime):
                           current_start = current_start.date()
                       
                       current_end = activity_data.get('end_date')
                       if isinstance(current_end, str):
                           current_end = datetime.strptime(current_end, '%Y-%m-%d').date()
                       elif isinstance(current_end, datetime):
                           current_end = current_end.date()
                       
                       edit_start_date = st.date_input("Start Date *", value=current_start)
                       edit_end_date = st.date_input("End Date *", value=current_end)
                   
                   with edit_col2:
                       # Handle resources and dependencies
                       current_resources = activity_data.get('resources', [])
                       if isinstance(current_resources, list):
                           resources_str = ', '.join(current_resources)
                       else:
                           resources_str = str(current_resources)
                       
                       current_dependencies = activity_data.get('dependencies', [])
                       if isinstance(current_dependencies, list):
                           dependencies_str = ', '.join(current_dependencies)
                       else:
                           dependencies_str = str(current_dependencies)
                       
                       edit_resources = st.text_input("Resources (comma-separated)", value=resources_str)
                       edit_dependencies = st.text_input("Dependencies (comma-separated)", value=dependencies_str)
                       edit_critical_path = st.checkbox("Critical Path", value=activity_data.get('critical_path', False))
                       edit_group_name = st.text_input("Group Name *", value=activity_data.get('group_name', ''))
                   
                   # Form buttons
                   edit_col1, edit_col2, edit_col3 = st.columns(3)
                   
                   with edit_col1:
                       update_submitted = st.form_submit_button("💾 Update Activity", type="primary")
                   with edit_col2:
                       cancel_edit = st.form_submit_button("❌ Cancel", type="secondary")
                   with edit_col3:
                       delete_submitted = st.form_submit_button("🗑️ Delete Activity", type="secondary")
                   
                   if update_submitted:
                       if edit_activity_name and edit_start_date and edit_end_date and edit_group_name:
                           # Validate date range
                           if edit_end_date < edit_start_date:
                               st.error("End date must be after start date")
                           else:
                               # Parse comma-separated lists
                               edit_resources_list = [r.strip() for r in edit_resources.split(',') if r.strip()] if edit_resources else []
                               edit_dependencies_list = [d.strip() for d in edit_dependencies.split(',') if d.strip()] if edit_dependencies else []
                               
                               updated_activity_data = {
                                   'activity_name': edit_activity_name,
                                   'description': edit_description or '',
                                   'start_date': edit_start_date,
                                   'end_date': edit_end_date,
                                   'resources': json.dumps(edit_resources_list),
                                   'dependencies': json.dumps(edit_dependencies_list),
                                   'critical_path': edit_critical_path,
                                   'group_name': edit_group_name
                               }
                               
                               success, message = update_activity(engine, activity_id, updated_activity_data)
                               if success:
                                   st.success(message)
                                   # Clear edit state
                                   st.session_state.show_edit_form = False
                                   del st.session_state.edit_activity_id
                                   st.rerun()
                               else:
                                   st.error(message)
                       else:
                           st.error("Please fill in all required fields marked with *")
                   
                   if cancel_edit:
                       # Clear edit state
                       st.session_state.show_edit_form = False
                       if 'edit_activity_id' in st.session_state:
                           del st.session_state.edit_activity_id
                       st.rerun()
                   
                   if delete_submitted:
                       # Confirm delete
                       if st.session_state.get('confirm_delete_edit') != activity_id:
                           st.session_state.confirm_delete_edit = activity_id
                           st.warning("⚠️ Click delete again to confirm")
                           st.rerun()
                       else:
                           success, message = delete_activity(engine, activity_id)
                           if success:
                               st.success(message)
                               # Clear edit state
                               st.session_state.show_edit_form = False
                               if 'edit_activity_id' in st.session_state:
                                   del st.session_state.edit_activity_id
                               if 'confirm_delete_edit' in st.session_state:
                                   del st.session_state.confirm_delete_edit
                               st.rerun()
                           else:
                               st.error(message)
           else:
               st.error("Activity not found")
               st.session_state.show_edit_form = False
               if 'edit_activity_id' in st.session_state:
                   del st.session_state.edit_activity_id
       
       else:
           st.info("Select an activity to edit from the 'Database Data' tab, or choose one below:")
           
           # Load current data for selection
           df, data_source = load_database_data_with_ids(engine)
           
           if not df.empty and 'id' in df.columns:
               st.markdown("**Select Activity to Edit:**")
               
               activity_options = []
               for _, row in df.iterrows():
                   activity_options.append(f"ID {row['id']}: {row['Activity Name']} ({row['Group']})")
               
               selected_activity_edit = st.selectbox("Choose activity:", ["Select an activity..."] + activity_options)
               
               if selected_activity_edit != "Select an activity...":
                   activity_id = int(selected_activity_edit.split(":")[0].replace("ID ", ""))
                   
                   col1, col2 = st.columns(2)
                   with col1:
                       if st.button("✏️ Edit This Activity", type="primary"):
                           st.session_state.edit_activity_id = activity_id
                           st.session_state.show_edit_form = True
                           st.rerun()
                   
                   with col2:
                       if st.button("👁️ Preview Activity Details"):
                           activity_data = get_activity_by_id(engine, activity_id)
                           if activity_data:
                               st.json(activity_data)
           else:
               st.info("No activities available to edit. Add some activities first!")
               
       # Show current database status
       st.markdown("---")
       with st.expander("ℹ️ Database Status"):
           if engine is not None and st.session_state.get('database_type') == 'postgresql':
               st.success("**PostgreSQL Database Connected**")
               st.write("- Changes are saved permanently")
               st.write("- Data persists across sessions")
           elif st.session_state.get('database_type') == 'session':
               st.warning("**Session Database Active**")
               st.write("- Changes are temporary (session only)")
               st.write("- Data will be lost when you refresh the page")
               st.write("- Consider exporting important data")
           else:
               st.error("**No Database Connection**")
               st.write("- Cannot save changes")
               st.write("- Please check database configuration")
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
                
    engine = init_database()
    activity_viewer_page(engine)
    