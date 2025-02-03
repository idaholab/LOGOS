# -*- coding: utf-8 -*-
"""
Created on Tue Jul 30 09:39:09 2024

@author: Edward Chen
@email: edward.chen@inl.gov
"""
import json
import ast

def load_json(file):
    with open(file, 'r') as f:
        data = json.load(f)
        f.close()
    
    return data

def load_json_src(file):
    act_list = list()
    with open(file, 'r') as f:
        for line in f.readlines():
            try:
                act_list.append(json.loads(json.loads(line)))
            except:
                continue
        f.close()
        
    return act_list

def get_Activity(file):
    pass

if __name__ == "__main__":
    import sys
    import pprint
    sys.path.append("../test/")
    item = load_json_src("../test/benchmarkOutageSchedule.json")
    pprint(item)
    