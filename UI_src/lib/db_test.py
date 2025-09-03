# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 17:31:09 2025

@author: ChEdw
"""
import sys
import os
from sqlalchemy import create_engine, text

def test_db(user=None, pw=None, dbName=None):
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = os.getenv('DB_PORT', '5432')  # See Docker-compose to edit default
    
    if dbName == None:
        db_name = os.getenv('DB_NAME', 'RAVEN_Application_DB') # See env to edit default
    else:
        db_name = dbName
        
    if user == None:
        db_user = os.getenv('DB_USER', 'admin') # See env to edit default
    else:
        db_user = user
    if pw == None:
        db_password = os.getenv('DB_PASSWORD', 'password') # See env to edit default
    else:
        db_password = pw
    
    print(f"User used: {db_user}")
    print(f"Password used: {db_password}")
    print(f"Database name used: {db_name}")
    
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
    
    print(version)
    print("Connection Succuessful!")
    
    
if __name__ == "__main__":
    test_db(sys.argv[1], sys.argv[2], sys.argv[3])
