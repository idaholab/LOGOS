# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 12:12:19 2025

@author: ChEdw
"""
# Default libraries
import os
from datetime import datetime

# Installed libraries
import streamlit as st
from sqlalchemy import create_engine, text
import pandas as pd

# Initialize session state for backup database
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
        
def load_data_from_db(engine):
    """Load all data from database with error handling"""
    if engine is None:
        return pd.DataFrame()
    
    try:
        query = """
        SELECT 
            activity_name as "Activity Name",
            description as "Description", 
            start_date as "Start Date",
            end_date as "End Date",
            resources as "Resources",
            dependencies as "Dependencies", 
            critical_path as "Critical Path",
            group_name as "Group",
            CASE 
                WHEN CURRENT_DATE < start_date THEN 'Not Started'
                WHEN CURRENT_DATE > end_date THEN 'Completed'
                ELSE 'In Progress'
            END as "Status",
            (end_date - start_date) as "Duration (Days)"
        FROM project_activities 
        ORDER BY start_date, activity_name;
        """
        
        df = pd.read_sql(query, engine)
        
        # Convert JSONB columns back to lists for display
        if not df.empty:
            df['Resources'] = df['Resources'].apply(lambda x: x if isinstance(x, list) else [])
            df['Dependencies'] = df['Dependencies'].apply(lambda x: x if isinstance(x, list) else [])
            df['Start Date'] = pd.to_datetime(df['Start Date'])
            df['End Date'] = pd.to_datetime(df['End Date'])
        
        return df
    
    except Exception as e:
        st.error(f"Error loading data: {str(e)}")
        return pd.DataFrame()
    
def get_session_stats():
    """Get statistics from session database"""
    if 'temp_database' not in st.session_state:
        return {}
    
    df = st.session_state.temp_database
    return {
        'total_activities': len(df),
        'critical_activities': len(df[df['critical_path'] == True]),
        'unique_groups': df['group_name'].nunique(),
        'completed': 0,  # Simplified for session data
        'in_progress': len(df),  # Simplified for session data
        'not_started': 0  # Simplified for session data
    }

def get_database_stats(engine):
    """Get comprehensive database statistics"""
    if engine is not None:
   
        try:
            with engine.connect() as conn:
                # Total activities
                result = conn.execute(text("SELECT COUNT(*) FROM project_activities")).fetchone()
                total_activities = result[0] if result else 0
                
                # Critical path activities
                result = conn.execute(text("SELECT COUNT(*) FROM project_activities WHERE critical_path = true")).fetchone()
                critical_activities = result[0] if result else 0
                
                # Unique groups
                result = conn.execute(text("SELECT COUNT(DISTINCT group_name) FROM project_activities")).fetchone()
                unique_groups = result[0] if result else 0
            
                # Activities by status
                result = conn.execute(text("""
                    SELECT 
                        SUM(CASE WHEN CURRENT_DATE < start_date THEN 1 ELSE 0 END) as not_started,
                        SUM(CASE WHEN CURRENT_DATE BETWEEN start_date AND end_date THEN 1 ELSE 0 END) as in_progress,
                        SUM(CASE WHEN CURRENT_DATE > end_date THEN 1 ELSE 0 END) as completed
                    FROM project_activities
                """)).fetchone()
            
                status_counts = {
                    'not_started': result[0] if result and result[0] else 0,
                    'in_progress': result[1] if result and result[1] else 0,
                    'completed': result[2] if result and result[2] else 0
                    }
            
                return {
                    'total_activities': total_activities,
                    'critical_activities': critical_activities,
                    'unique_groups': unique_groups,
                    **status_counts
                    }
    
        except Exception as e:
            st.error(f"Error getting statistics: {str(e)}")
            return {}
    else:
        # Session database statistics
        return get_session_stats()

# Database configuration
@st.cache_resource
def init_database():
    """Initialize database connection with Windows-compatible settings"""
    try:
        # Get database credentials from environment variables
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')  # See Docker-compose to edit default
        db_name = os.getenv('DB_NAME', 'postgres') # See env to edit default
        db_user = os.getenv('DB_USER', 'postgres') # See env to edit default
        db_password = os.getenv('DB_PASSWORD', 'SecureHash234E') # See env to edit default
        
        # Create connection string with additional parameters for stability
        connection_string = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?sslmode=prefer&connect_timeout=10"
        
        # Create SQLAlchemy engine with connection pooling
        engine = create_engine(
            connection_string,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800
        )
        
        # Test connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            st.sidebar.success(f"✅ Connected to PostgreSQL Version: {version}")
            st.sidebar.info(f"Database: {db_name}")
            st.session_state['database_type'] = 'postgresql'        
        return engine
    
    except Exception as e:
        st.warning(f"⚠️ PostgreSQL connection failed: {str(e)}")
        st.info("🔄 Switching to temporary session database...")
        st.session_state['database_type'] = 'session'
        init_session_database()
        return None