# -*- coding: utf-8 -*-
"""
Created on Mon Aug 19 08:26:31 2024

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
    from Import_func import load_json
    from RAVEN_func import is_critical 
    
    raw  = load_json("./test_day_group_v2.json")
    data = pd.DataFrame.from_dict(raw)
    data["CP_flag"] = False
    
    # Preprocessing
    data["Start"] = pd.to_datetime(data["Start"]).dt.date
    data["Finish"] = pd.to_datetime(data["Finish"]).dt.date
    data["Critical"] = data.apply(is_critical, axis=1)
    
    hover_template = "Activity Name: %{x}" + "<br>Start Date: %{base|%Y-%m-%d}" + "<br>End Date: %{x|%Y-%m-%d}" + "<br>Task: %{y}"
                                    
    # User option
    u_opt = "All"
    
    if u_opt == "All":
        data["Task"] = data["Task"] + "_" + data["Group"]
        data["Adj"]  = data.apply(relabel, axis=1)
        sub_data = data
    else:
        sub_data = data[data["Group"]==u_opt]
    
    # Plot Gantt Chart
    fig = px.timeline(sub_data,
                      y=sub_data["Task"],
                      x_start=sub_data["Start"],
                      x_end=sub_data["Finish"],
                      color="Critical",
                      opacity=0.5
                      )

    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=True)
    fig.update_layout(showlegend=False)
    
    fig.update_traces(hovertemplate=hover_template,
                      text= sub_data["Group"])
    
    start_date = sub_data["Start"].iloc[0]
    end_date   = sub_data["Finish"].iloc[-1]
    numdays    = (end_date - start_date).days
    
    fig.update_layout(
        xaxis = dict(
            tickmode = 'array',
            tickvals = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
            ticktext = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
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
            autorange = "reversed"
        )
    )
    
    fig.update_traces(width=1)

    
    fig = Draw.draw_task_links(fig, sub_data)

    numweeks = math.ceil(numdays/7)
    fig.update_layout(
        xaxis = dict(
            tickmode = 'array',
            tickvals = [start_date + datetime.timedelta(weeks=x) for x in range(numweeks)],
            ticktext = [start_date + datetime.timedelta(weeks=x) for x in range(numweeks)],
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