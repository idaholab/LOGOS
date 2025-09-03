-- Set timezone
SET timezone = 'UTC';

-- Create the main table with proper constraints
CREATE TABLE IF NOT EXISTS project_activities (
    id SERIAL PRIMARY KEY,
    activity_name TEXT NOT NULL CHECK (length(activity_name) > 0),
    description TEXT DEFAULT '',
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    resources JSONB DEFAULT '[]'::jsonb,
    dependencies JSONB DEFAULT '[]'::jsonb,
    critical_path BOOLEAN DEFAULT FALSE,
    group_name TEXT NOT NULL CHECK (length(group_name) > 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Add constraint to ensure end_date is after start_date
    CONSTRAINT valid_date_range CHECK (end_date >= start_date)
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_activity_name ON project_activities(activity_name);
CREATE INDEX IF NOT EXISTS idx_group_name ON project_activities(group_name);
CREATE INDEX IF NOT EXISTS idx_critical_path ON project_activities(critical_path);
CREATE INDEX IF NOT EXISTS idx_start_date ON project_activities(start_date);
CREATE INDEX IF NOT EXISTS idx_end_date ON project_activities(end_date);
CREATE INDEX IF NOT EXISTS idx_date_range ON project_activities(start_date, end_date);

-- Create GIN indexes for JSONB columns for efficient searching
CREATE INDEX IF NOT EXISTS idx_resources_gin ON project_activities USING GIN (resources);
CREATE INDEX IF NOT EXISTS idx_dependencies_gin ON project_activities USING GIN (dependencies);

-- Function to update the updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to automatically update updated_at
DROP TRIGGER IF EXISTS update_project_activities_updated_at ON project_activities;
CREATE TRIGGER update_project_activities_updated_at 
    BEFORE UPDATE ON project_activities 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Grant all necessary permissions
GRANT ALL PRIVILEGES ON TABLE project_activities TO admin;
GRANT USAGE, SELECT ON SEQUENCE project_activities_id_seq TO admin;
GRANT EXECUTE ON FUNCTION update_updated_at_column() TO admin;

-- Create a view for easy querying
CREATE OR REPLACE VIEW project_activities_view AS
SELECT 
    id,
    activity_name,
    description,
    start_date,
    end_date,
    resources,
    dependencies,
    critical_path,
    group_name,
    created_at,
    updated_at,
    (end_date - start_date) as duration_days,
    CASE 
        WHEN CURRENT_DATE < start_date THEN 'Not Started'
        WHEN CURRENT_DATE > end_date THEN 'Completed'
        ELSE 'In Progress'
    END as status
FROM project_activities;

GRANT SELECT ON project_activities_view TO admin;