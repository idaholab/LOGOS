# -*- coding: utf-8 -*-
"""
Created on Mon Aug 19 08:26:31 2024

@author: CHENE
"""

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
    
    data = pd.DataFrame.from_dict(load_json("./test_week.json"))
    
    # Preprocessing

    data["Start"] = pd.to_datetime(data["Start"]).dt.date
    data["Finish"] = pd.to_datetime(data["Finish"]).dt.date
    data["Critical"] = data.apply(is_critical, axis=1)
    
    hover_template = 'Activity Name: %{x} <br>Start Date: %{x_start} <br>End Date: %{x_end}'
    
    fig = px.timeline(data,
                      y=data["Task"],
                      x_start=data["Start"],
                      x_end=data["Finish"],
                      color="Critical"
                      )

    fig.update_xaxes(showgrid=True)
    fig.update_yaxes(showgrid=True)
    fig.update_layout(showlegend=False)
    
    # x_start = base, x_end = x in figure dictionary
    fig.update_traces(hovertemplate="Start: %{base|%Y-%m-%d}<br>"
                                    "End: %{x|%Y-%m-%d}<br>"
                                    "Task: %{y}"
                                    )
    
    start_date = data["Start"][0]
    end_date   = data["Finish"].iloc[-1]
    numdays    = (end_date - start_date).days
    
    fig.update_layout(
        xaxis = dict(
            tickmode = 'array',
            tickvals = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
            ticktext = [start_date + datetime.timedelta(days=x) for x in range(numdays)],
            range    = [start_date, end_date]
        )
    )
    
    tickvals = np.arange(0, len(data))
    fig.update_layout(
        yaxis = dict(
            tickmode = 'array',
            tickvals = tickvals,
            ticktext = data['Task'],
            range  = [tickvals[0]-1/2, tickvals[-1]+1/2],
            autorange = "reversed"
        )
    )
    
    fig.update_traces(width=1)

    
    fig = Draw.draw_task_links(fig, data)

    numweeks = math.ceil(numdays/7)
    fig.update_layout(
        xaxis = dict(
            tickmode = 'array',
            tickvals = [start_date + datetime.timedelta(weeks=x) for x in range(numweeks)],
            ticktext = [start_date + datetime.timedelta(weeks=x) for x in range(numweeks)],
            range    = [start_date, end_date]
        )
    )
    
    tickvals = np.arange(0, len(data))
    fig.update_layout(
        yaxis = dict(
            tickmode = 'array',
            tickvals = tickvals,
            ticktext = data['Task'],
            range  = [tickvals[0]-1/2, tickvals[-1]+1/2],
            autorange = "reversed",
            categoryarray = data["Task"]
        )
    )
    plotly.offline.plot(fig)