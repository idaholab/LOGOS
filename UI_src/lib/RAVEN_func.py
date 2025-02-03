# -*- coding: utf-8 -*-
"""
Created on Tue Jul 30 09:14:51 2024

@author: Edward Chen
@email: edward.chen@inl.gov
"""

def is_critical(json_dict):
    if json_dict["CP_flag"]==True:
        return "Is Critical"
    else:
        return "Not Critical"