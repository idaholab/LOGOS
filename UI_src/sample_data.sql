-- Insert comprehensive sample data
INSERT INTO project_activities (
    activity_name, 
    description, 
    start_date, 
    end_date, 
    resources, 
    dependencies, 
    critical_path, 
    group_name
) VALUES 

-- Planning Phase
(
    'Project Initiation',
    'Define project scope, objectives, and initial requirements gathering',
    '2024-01-01',
    '2024-01-10',
    '["Project Manager", "Stakeholders", "Business Analyst", "Conference Room"]'::jsonb,
    '[]'::jsonb,
    true,
    'Management'
),
(
    'Requirements Analysis',
    'Detailed analysis and documentation of functional and non-functional requirements',
    '2024-01-11',
    '2024-01-25',
    '["Business Analyst", "Domain Expert", "Requirements Tool", "Interview Sessions"]'::jsonb,
    '["Project Initiation"]'::jsonb,
    true,
    'Analysis Team'
),

-- Design Phase
(
    'System Architecture Design',
    'Design overall system architecture and technology stack selection',
    '2024-01-26',
    '2024-02-10',
    '["Solution Architect", "Senior Developer", "Architecture Tools", "Design Review"]'::jsonb,
    '["Requirements Analysis"]'::jsonb,
    true,
    'Architecture Team'
),
(
    'Database Schema Design',
    'Design database schema, relationships, and data models',
    '2024-02-01',
    '2024-02-15',
    '["Database Architect", "Data Modeler", "ERD Tools", "Database Server"]'::jsonb,
    '["System Architecture Design"]'::jsonb,
    true,
    'Database Team'
),
(
    'UI/UX Design',
    'Create user interface mockups, wireframes, and user experience flows',
    '2024-02-05',
    '2024-02-25',
    '["UI Designer", "UX Designer", "Design Software", "User Research"]'::jsonb,
    '["Requirements Analysis"]'::jsonb,
    false,
    'Design Team'
),

-- Development Phase  
(
    'Backend API Development',
    'Develop REST APIs, business logic, and data access layers',
    '2024-02-16',
    '2024-03-20',
    '["Backend Developer", "Senior Developer", "API Framework", "Testing Tools", "Development Environment"]'::jsonb,
    '["Database Schema Design"]'::jsonb,
    true,
    'Backend Team'
),
(
    'Frontend Development',
    'Develop user interface components and integrate with backend APIs',
    '2024-02-26',
    '2024-03-25',
    '["Frontend Developer", "UI Developer", "JavaScript Framework", "Development Tools"]'::jsonb,
    '["UI/UX Design", "Backend API Development"]'::jsonb,
    true,
    'Frontend Team'
),
(
    'Database Implementation',
    'Set up production database, implement schemas, and create initial data',
    '2024-03-01',
    '2024-03-10',
    '["Database Administrator", "Database Server", "Migration Scripts", "Backup Tools"]'::jsonb,
    '["Database Schema Design"]'::jsonb,
    false,
    'Database Team'
),

-- Testing Phase
(
    'Unit Testing',
    'Write and execute unit tests for individual components',
    '2024-03-10',
    '2024-03-30',
    '["Developer", "Testing Framework", "Code Coverage Tools", "Automated Testing"]'::jsonb,
    '["Backend API Development"]'::jsonb,
    false,
    'Development Team'
),
(
    'Integration Testing',
    'Test integration between different system components',
    '2024-03-21',
    '2024-04-05',
    '["QA Engineer", "Test Environment", "Integration Tools", "API Testing Tools"]'::jsonb,
    '["Frontend Development", "Backend API Development"]'::jsonb,
    true,
    'QA Team'
),
(
    'System Testing',
    'Comprehensive testing of the complete system functionality',
    '2024-04-06',
    '2024-04-20',
    '["QA Manager", "Test Team", "Testing Environment", "Test Cases", "Bug Tracking"]'::jsonb,
    '["Integration Testing"]'::jsonb,
    true,
    'QA Team'
),
(
    'Performance Testing',
    'Load testing, stress testing, and performance optimization',
    '2024-04-10',
    '2024-04-25',
    '["Performance Tester", "Load Testing Tools", "Monitoring Tools", "Performance Environment"]'::jsonb,
    '["System Testing"]'::jsonb,
    false,
    'Performance Team'
),

-- Documentation & Training
(
    'Technical Documentation',
    'Create API documentation, system documentation, and deployment guides',
    '2024-03-15',
    '2024-04-30',
    '["Technical Writer", "Documentation Tools", "Subject Matter Expert", "Review Process"]'::jsonb,
    '["Backend API Development"]'::jsonb,
    false,
    'Documentation Team'
),
(
    'User Manual Creation',
    'Create end-user manuals, tutorials, and help documentation',
    '2024-04-01',
    '2024-04-20',
    '["Technical Writer", "UX Writer", "Screen Capture Tools", "Content Management"]'::jsonb,
    '["Frontend Development"]'::jsonb,
    false,
    'Documentation Team'
),
(
    'User Training Program',
    'Design and conduct user training sessions and create training materials',
    '2024-04-21',
    '2024-05-05',
    '["Trainer", "Training Materials", "Training Room", "Training Environment"]'::jsonb,
    '["User Manual Creation"]'::jsonb,
    false,
    'Training Team'
),

-- Deployment Phase
(
    'Production Environment Setup',
    'Set up production servers, networking, and security configurations',
    '2024-04-15',
    '2024-04-28',
    '["DevOps Engineer", "System Administrator", "Production Servers", "Security Tools"]'::jsonb,
    '["Performance Testing"]'::jsonb,
    true,
    'DevOps Team'
),
(
    'Application Deployment',
    'Deploy application to production environment with rollback procedures',
    '2024-04-29',
    '2024-05-03',
    '["DevOps Engineer", "Deployment Scripts", "Monitoring Tools", "Rollback Plan"]'::jsonb,
    '["Production Environment Setup", "System Testing"]'::jsonb,
    true,
    'DevOps Team'
),
(
    'Go-Live Support',
    'Provide immediate support during initial production launch',
    '2024-05-04',
    '2024-05-10',
    '["Support Team", "On-Call Engineers", "Monitoring Dashboard", "Communication Tools"]'::jsonb,
    '["Application Deployment", "User Training Program"]'::jsonb,
    true,
    'Support Team'
),

-- Post-Launch
(
    'Post-Launch Monitoring',
    'Monitor system performance, user adoption, and issue resolution',
    '2024-05-11',
    '2024-05-31',
    '["Support Team", "Monitoring Tools", "Analytics Platform", "Issue Tracking"]'::jsonb,
    '["Go-Live Support"]'::jsonb,
    false,
    'Support Team'
),
(
    'Project Closure',
    'Project retrospective, documentation handover, and resource release',
    '2024-06-01',
    '2024-06-07',
    '["Project Manager", "Team Leads", "Documentation Repository", "Lessons Learned"]'::jsonb,
    '["Post-Launch Monitoring"]'::jsonb,
    false,
    'Management'
);

-- Insert some additional data for different groups and scenarios
INSERT INTO project_activities (
    activity_name, 
    description, 
    start_date, 
    end_date, 
    resources, 
    dependencies, 
    critical_path, 
    group_name
) VALUES 
(
    'Security Audit',
    'Comprehensive security assessment and penetration testing',
    '2024-04-01',
    '2024-04-15',
    '["Security Consultant", "Penetration Testing Tools", "Security Scanner"]'::jsonb,
    '["Integration Testing"]'::jsonb,
    false,
    'Security Team'
),
(
    'Compliance Review',
    'Ensure application meets regulatory and compliance requirements',
    '2024-04-10',
    '2024-04-24',
    '["Compliance Officer", "Legal Team", "Audit Tools", "Compliance Framework"]'::jsonb,
    '["Security Audit"]'::jsonb,
    false,
    'Compliance Team'
),
(
    'Data Migration',
    'Migrate existing data from legacy systems to new application',
    '2024-03-20',
    '2024-04-10',
    '["Data Engineer", "Migration Tools", "Data Validation Scripts", "Legacy System Access"]'::jsonb,
    '["Database Implementation"]'::jsonb,
    true,
    'Data Team'
);