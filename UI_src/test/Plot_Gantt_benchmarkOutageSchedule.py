# -*- coding: utf-8 -*-
"""
Created on Thu Sep 12 07:56:54 2024

@author: CHENE
"""

def relabel(json_list):
    group_tag = json_list["Group"]
    dest_Adj = json_list["Adj"]
    newlist = list()
    for line in dest_Adj:
        newlist.append(line + "_" + group_tag)
    
    return newlist
    

if __name__ == "__main__":
    import plotly.express as px
    import plotly
    import datetime
    import numpy as np
    import pandas as pd
    import math 
    import sys
    
    sys.path.append("../lib/")
    import Draw
    from Import_func import load_json_src
    from RAVEN_func import is_critical 
    
    raw  = load_json_src("./benchmarkOutageSchedule_large.json")
    # data_list = list()
    # for key in raw.keys():
    #     for row in raw[key]:
    #         row["Group"] = key
    #         row["CP_flag"] = False
    #         data_list.append(row)
    
    # data = pd.DataFrame.from_dict(data_list)
    
    # # Preprocessing
    # data["Start"] = pd.to_datetime(data["Start"]).dt.date
    # data["Finish"] = pd.to_datetime(data["Finish"]).dt.date
    # data["Critical"] = data.apply(is_critical, axis=1)
    
    # hover_template = "Activity Name: %{x}" + "<br>Start Date: %{base|%Y-%m-%d}" + "<br>End Date: %{x|%Y-%m-%d}" + "<br>Task: %{y}"
                                    
    # # User option
    # u_opt = "Maintenance"
    
    # if u_opt == "All":
    #     data["Task"] = data["Task"] + "_" + data["Group"]
    #     data["Adj"]  = data.apply(relabel, axis=1)
    #     sub_data = data
    # else:
    #     sub_data = data[data["Group"]==u_opt]
    
    sub_data = pd.DataFrame.from_dict(raw) 
    format = "%Y-%m-%d %H:%M:%S"
    sub_data["startTime"] = pd.to_datetime(sub_data["startTime"], format="ISO8601")
    sub_data["endTime"] = pd.to_datetime(sub_data["endTime"], format="ISO8601")
    sub_data["Adj"] = sub_data["childs"]
    sub_data["Task"] = sub_data["name"]
    sub_data["CP_flag"] = sub_data["belongsToCP"]
    sub_data["Start"] = sub_data["startTime"]
    sub_data["Finish"] = sub_data["endTime"]
    
    # Plot Gantt Chart
    fig = px.timeline(sub_data[:10],
                      y=sub_data["Task"],
                      x_start=sub_data["Start"],
                      x_end=sub_data["Finish"]
                      )

    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=True)
    fig.update_layout(showlegend=False)
    
    # fig.update_traces(hovertemplate=hover_template,
    #                   text= sub_data["Group"])
    
    start_date = sub_data["startTime"].iloc[0]
    end_date   = sub_data["endTime"].iloc[-1]
    td         = end_date - start_date
    numhours   = td.days*24 + td.seconds//3600
    
    # fig.update_layout(
    #     xaxis = dict(
    #         tickmode = 'array',
    #         tickvals = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
    #         ticktext = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
    #         range    = [start_date, end_date]
    #     )
    # )
    
    # tickvals = np.arange(0, len(sub_data))
    # fig.update_layout(
    #     yaxis = dict(
    #         tickmode = 'array',
    #         tickvals = tickvals,
    #         ticktext = sub_data['Task'],
    #         range  = [tickvals[0]-1/2, tickvals[-1]+1/2],
    #         autorange = "reversed"
    #     )
    # )
    
    fig.update_traces(width=1)

    
   # fig = Draw.draw_task_links(fig, sub_data)
    
    fig.update_layout(
        xaxis = dict(
            tickmode = 'array',
            tickvals = [start_date + datetime.timedelta(hours=x) for x in range(numhours)],
            ticktext = [start_date + datetime.timedelta(hours=x) for x in range(numhours)],
            range    = [start_date, end_date]
        )
    )
    
    tickvals = np.arange(0, len(sub_data))
    fig.update_layout(
        yaxis = dict(
            tickmode = 'array',
            tickvals = tickvals,
            ticktext = sub_data['Task'],
            range  = [tickvals[0]-1/2, tickvals[-1]+1/2],
            autorange = "reversed",
            categoryarray = sub_data["Task"]
        )
    )
    plotly.offline.plot(fig)