# -*- coding: utf-8 -*-
"""
Created on Tue Jul 30 09:01:12 2024

@author: Edward Chen
@email: edward.chen@inl.gov
"""

# Default modules
import sys
import anvil.server

sys.path.append('./lib')

# Developed modules
import RAVEN_func

@anvil.server.callable
def calculateSimplifiedGantt(source_dict):
    '''
    Takes a source dictionary of gantt activities and converts it to a simplified 
    gantt chart

    Parameters
    ----------
    source_dict : dict
        Contains a dictionary of activities and contains the following fields:
            - "Task" : <string> Name of task.
            - "Start" : <date> Simple object of start of task.
            - "Finish" : <date> Simple object of end of task.
            - "Duration" : <int> Duration in days of task.
            - "Adj" : <string list> List of activities that follow the current.
            - "Resource" : <string list> List of resources current activity depends on.
            - "Description" : <string> Description of the task.
            - "CP_flag" : <bool> Identifies if the current task is on the critical path.

    Returns
    -------
    ret_dict : dict
        Contains a dictionary of new activities and their fields. Must follow a similar 
        dict format as the input dict. See above for field details. 

    '''
    # Currently this function does not do the intended thing. Is a placeholder for the real function.
    
    # Add your funtion here
    
    # Example output, replace with the calculated dictionary
    ret_dict = [{
        		"Task": "Start",
        		"Start": "2023-01-01",
        		"Finish": "2023-01-06",
        		"Duration": 5,
        		"Adj":["act23","act56","act7"],
        		"Resource":["Electrical", "Mechanical"],
        		"Description": "Beginning Task",
                "CP_flag": True
        	},
        	{
        		"Task": "act23",
        		"Start": "2023-01-06",
        		"Finish": "2023-01-11",
        		"Duration": 5,
        		"Adj":["act4"],
        		"Resource":["Human", "Mechanical", "Electrical"],
        		"Description": "Task Description",
                "CP_flag": False
        	},
        	{
        		"Task": "act4",
        		"Start": "2023-01-13",
        		"Finish": "2023-01-16",
        		"Duration": 3,
        		"Adj":["act8","act9"],
        		"Resource":["Human", "Mechanical"],
        		"Description": "Task Description",
                "CP_flag": True
        	},
        	{
        		"Task": "act56",
        		"Start": "2023-01-06",
        		"Finish": "2023-01-13",
        		"Duration": 4,
        		"Adj":["act4"],
        		"Resource":["Electrical", "Human", "Mechanical"],
        		"Description": "Task Description",
                "CP_flag": True
        	},
        	{
        		"Task": "act7",
        		"Start": "2023-01-06",
        		"Finish": "2023-01-12",
        		"Duration": 6,
        		"Adj":["act4"],
        		"Resource":["Electrical", "Human"],
        		"Description": "Task Description",
                "CP_flag": False
        	},
        	{
        		"Task": "act8",
        		"Start": "2023-01-16",
        		"Finish": "2023-01-18",
        		"Duration": 2,
        		"Adj":["end"],
        		"Resource":["Human", "Mechanical"],
        		"Description": "Task Description",
                "CP_flag": False
        	},
        	{
        		"Task": "act9",
        		"Start": "2023-01-16",
        		"Finish": "2023-01-19",
        		"Duration": 3,
        		"Adj":["end"],
        		"Resource":["Electrical", "Human"],
        		"Description": "Task Description",
                "CP_flag": True
        	},
        	{
        		"Task": "end",
        		"Start": "2023-01-19",
        		"Finish": "2023-01-21",
        		"Duration": 2,
        		"Adj":[],
        		"Resource":["Human"],
        		"Description": "Task Description",
                "CP_flag": True
        	}
        ]
    
    return ret_dict 

anvil.server.connect("server_KU62CM7OZKU4U3YLNIU7AO4Y-7LRIGGZ3CGPRCJEO")
anvil.server.wait_forever()