import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
import anvil.email
import anvil.server
import datetime as dt

import json
import pandas as pd
import plotly.express as px 
import numpy as np
import math

################################################### MAIN ###################################################
@anvil.server.callable
def get_all_tables():
  resource_table = app_tables.resource_table.search()
  activity_table = app_tables.activity_table.search()
  json_table = app_tables.json_table.search(tables.order_by("Start"))
  increment  = app_tables.increment.search()
  group_table = app_tables.group_table.search()
  return resource_table, activity_table, json_table, increment, group_table

################################################ END MAIN ###################################################

################################################ RESOURCE ###################################################
@anvil.server.callable
def add_resource(name, description):
  app_tables.resource_table.add_row(resource_name=name,
                                    resource_description=description)

@anvil.server.callable
def edit_resource(table_entry, name, description):
  table_entry.update(resource_name=name,
                     resource_description=description)

@anvil.server.callable
def delete_resource(table_entry):
  table_entry.delete()
  
############################################ END RESOURCE ###################################################

################################################ ACTIVITY ###################################################
@anvil.server.callable
def add_activity(**kwargs):
  Duration = (kwargs["Finish"] - kwargs["Start"]).days
  app_tables.json_table.add_row(Task=kwargs["Task"],
                                Description=kwargs["Description"],
                                Start=kwargs["Start"],
                                Finish=kwargs["Finish"],
                                Duration=Duration,
                                Resource=kwargs["Resource"],
                                Adj=kwargs["Adj"],
                                Group=kwargs["Group"],
                                CP_flag=kwargs["CP_flag"]
                               )
  app_tables.json_table.search(tables.order_by("Start", ascending=False))
  
@anvil.server.callable
def edit_activity(table_entry, **kwargs):
  Duration = (kwargs["Start"] - kwargs["Finish"]).days
  table_entry.update(Task=kwargs["Task"],
                     Description=kwargs["Description"],
                     Start=kwargs["Start"],
                     Finish=kwargs["Finish"],
                     Duration=Duration,
                     Resource=kwargs["Resource"],
                     Adj=kwargs["Adj"],
                     Group=kwargs["Group"],
                     CP_flag=kwargs["CP_flag"]
                    )
  app_tables.json_table.search(tables.order_by("Start", ascending=False))

@anvil.server.callable
def delete_activity(table_entry):
  table_entry.delete()

############################################ END ACTIVITY ###################################################

################################################ GROUP ######################################################
@anvil.server.callable
def add_group(group_name, group_description):
  app_tables.group_table.add_row(group_name=group_name,
                                 group_description=group_description
                               )
  
@anvil.server.callable
def edit_group(table_entry, group_name, group_description):
  table_entry.update(group_name=group_name,
                     group_description = group_description
                    )

@anvil.server.callable
def delete_group(table_entry):
  table_entry.delete()

############################################### END GROUP ###################################################

################################################ SCHEDULE ###################################################

# Backend for Schedule
@anvil.server.callable
def draw_simplified_chart(start_date=0, end_date=0, interval="Days", showCrit=False, showArrow=True, Group="All"):
    '''
    Draws a Gantt chart using the simplified list of activities.

    Parameters
    ----------
    start_date : date
        Custom start date specified by user.
    end_date : date
        Custom end date specified by user.
    interval : string
        Custom interval specified by user.
    showCrit : Bool
        Specifies whether to display critical path
    showArrow : Bool
        Specifies whether to display activity link arrows
    Group : string
        Specifies whether to display a specific grouping in the Gantt chart
    Returns
    -------
    fig : plotly.figure
        Figure generated through plotly for Gantt display.
    '''
    data = get_data()
  
    data = anvil.server.call("calculateSimplifiedGantt", input)
  
    data = pd.DataFrame.from_dict(data)
    data = format_data(data)
  
    fig = draw_chart(data, start_date=start_date, end_date=end_date, interval=interval, showCrit=showCrit, showArrow=showArrow, Group=Group)
    return fig
  
@anvil.server.callable
def draw_full_chart(start_date=0, end_date=0, interval="Days", showCrit=False, showArrow=True, Group="All"):
    '''
    Draws a Gantt chart using the full list of activities.

    Parameters
    ----------
    start_date : date
        Custom start date specified by user.
    end_date : date
        Custom end date specified by user.
    interval : string
        Custom interval specified by user.
    showCrit : Bool
        Specifies whether to display critical path
    Group : string
        Specifies whether to display a specific grouping in the Gantt chart
    Returns
    -------
    fig : plotly.figure
        Figure generated through plotly for Gantt display.
    '''
    data = get_data(ret_pd=True)
    data = format_data(data)  
    
    fig = draw_chart(data, start_date=start_date, end_date=end_date, interval=interval, showCrit=showCrit, showArrow=showArrow, Group=Group)
    return fig

@anvil.server.callable
def load_json(file):
  # Try to load the file
  f = file.get_bytes().decode('utf-8').replace("'", '"')
  
  try:
    data = json.loads(f)
    data = data["Group"] 
    data_list = list()
    for key in data.keys():
        for row in data[key]:
            row["Group"] = key
            row["CP_flag"] = False
            data_list.append(row)
    data = pd.DataFrame.from_dict(data_list)
  except Exception as e:
    print("Could not load JSON file: " + str(e))
    return 0
  
  # Remove existing table from anvil
  app_tables.json_table.delete_all_rows()
  
  # Format data correctly
  data["Start"] = pd.to_datetime(data["Start"]).dt.date
  data["Finish"] = pd.to_datetime(data["Finish"]).dt.date
  data["Duration"] = pd.to_numeric(data["Duration"])
  
  for row in data.iterrows():
    tmp = row[1]
    app_tables.json_table.add_row(Task=tmp["Task"],
                                  Start=tmp["Start"],
                                  Finish=tmp["Finish"],
                                  Duration=tmp["Duration"],
                                  Adj=tmp["Adj"],
                                  Description=tmp["Description"],
                                  Resource=tmp["Resource"],
                                  CP_flag=tmp["CP_flag"],
                                  Group=tmp["Group"]
                                 )

  # Add resources to separate unique table
  update_resource_table()
  update_group_table()
  return 1

@anvil.server.callable
def load_file(file):
  # Try to load the file
  f = file.get_bytes().decode('utf-8').replace("'", '"')
  
  try:
    data = json.loads(f) 
    data = pd.DataFrame.from_dict(data)
    data["CP_flag"] = False
  except Exception as e:
    print("Could not load JSON file: " + str(e))
    return 0
  
  # Remove existing table from anvil
  app_tables.json_table.delete_all_rows()
  
  # Format data correctly
  data["Start"] = pd.to_datetime(data["Start"]).dt.date
  data["Finish"] = pd.to_datetime(data["Finish"]).dt.date
  data["Duration"] = pd.to_numeric(data["Duration"])
  
  for row in data.iterrows():
    tmp = row[1]
    app_tables.json_table.add_row(Task=tmp["Task"],
                                  Start=tmp["Start"],
                                  Finish=tmp["Finish"],
                                  Duration=tmp["Duration"],
                                  Adj=tmp["Adj"],
                                  Description=tmp["Description"],
                                  Resource=tmp["Resource"],
                                  CP_flag=tmp["CP_flag"],
                                  Group=tmp["Group"]
                                 )

  # Add resources to separate unique table
  update_resource_table()
  update_group_table()
  return 1  

def draw_chart(data, start_date=0, end_date=0, interval="Days", showCrit=False, showArrow=True, Group="All"):

  # Using CP_flag, determine critical activities for Gantt coloring
  data["Critical"] = data.apply(is_critical, axis=1)

  # Same activity names will overlap in the Gantt chart and not be visible. Activities are renamed by group. 
  if Group == "All":
    data["Task"] = data["Task"] + "_" + data["Group"]
    data["Adj"]  = data.apply(relabel, axis=1)
  else:
    data = data[data["Group"]==Group]
      
  # Draw Gantt Chart
  if showCrit:
    fig = px.timeline(data,
                      y=data["Task"],
                      x_start=data["Start"],
                      x_end=data["Finish"],
                      color="Critical"
    )
    fig.update_layout(legend_title_text='Critical Task Status')
  else:
    fig = px.timeline(data,
                      y=data["Task"],
                      x_start=data["Start"],
                      x_end=data["Finish"]
    )
    
  # Update x-axis and y-axis with correct information
  fig.update_xaxes(showgrid=True)
  fig.update_yaxes(showgrid=True)
  
  hover_template = "Task Name: %{y}" + "<br>Start Date: %{base|%Y-%m-%d}" + "<br>End Date: %{x|%Y-%m-%d}" + "<br>Task: %{y}"

  fig.update_traces(hovertemplate=hover_template,
                    text=data["Group"]
                   )

  # Option to change start date for Gantt drawing
  if not isinstance(start_date, dt.date):
    start_date = data["Start"].iloc[0]
  else:
    start_date = start_date
    
  # Option to change end date for Gantt drawing
  if not isinstance(end_date, dt.date):
    end_date   = data["Finish"].iloc[-1]
  else:
    end_date = end_date
    
  numdays    = (end_date - start_date).days

  # Get range for y (# of activities)
  tickvals   = np.arange(0, len(data))
    
  fig.update_layout(
    xaxis = dict(tickmode = 'array',
                 tickvals = [start_date + dt.timedelta(days=x) for x in range(numdays)],
                 ticktext = [start_date + dt.timedelta(days=x) for x in range(numdays)],
                 range    = [start_date, end_date]
    ),
    yaxis = dict(tickmode = 'array',
                 tickvals = tickvals,
                 ticktext = data['Task'],
                 range  = [tickvals[0]-1/2, tickvals[-1]+1/2],
                 autorange = "reversed",
                 categoryarray = data["Task"]
    )
  )
  
  # Change width of activities
  fig.update_traces(width=1)

  # Draw arrows between tasks. Must be after x- and y-axis intervals are specified to know where start and end arrow coordinates are.
  if showArrow:
    fig = draw_task_links(fig, data, showCrit=showCrit)
  
  # Convert to correct time scale
  if interval == "Weeks":
    numweeks = math.ceil(numdays/7)
    fig.update_layout(
      xaxis = dict(tickmode = 'array',
                  tickvals = [start_date + dt.timedelta(weeks=x) for x in range(numweeks)],
                  ticktext = [start_date + dt.timedelta(weeks=x) for x in range(numweeks)],
                  range    = [start_date, end_date]
      )
    )      
  elif interval == "Months":
    nummonths = math.ceil(numdays/28)
    fig.update_layout(
      xaxis = dict(tickmode = 'array',
                  tickvals = [start_date + dt.timedelta(weeks=x*4) for x in range(nummonths)],
                  ticktext = [start_date + dt.timedelta(weeks=x*4) for x in range(nummonths)],
                  range    = [start_date, end_date]
      )
    )     
  return fig

def draw_task_links(fig, json_dict, showCrit=False):
    '''
    Draw all arrows in the figure.

    Parameters
    ----------
    fig : plotly.figure
        Figure to draw the arrows on.
    json_dict : dict
        Dictionary of activities.

    Returns
    -------
    new_fig : plotly.figure
        New figure with arrows

    '''
    new_fig = fig
    
    # For each activity draw an arrow
    for link in json_dict.iterrows():
        start_act = link[1]
        
        # For each activity linked to current activity
        for endPoint in link[1]["Adj"]:
            ind     = json_dict.index[json_dict["Task"]==endPoint].tolist()
            end_act = json_dict.loc[ind[0]] 
            if not showCrit:
                new_fig = draw_arrow_between_jobs_v2(new_fig, start_act, end_act)
            elif showCrit and (end_act["CP_flag"] and start_act["CP_flag"]):
                new_fig = draw_arrow_between_jobs_v2(new_fig, start_act, end_act, color="black", width=3)
                
    return new_fig

def draw_arrow_between_jobs_v1(fig, first_job_dict, second_job_dict, color="blue", width=2):
    '''
    Draws an arrow between two gantt activities on a plotly chart. Requires timing
    of the activity to determine start and end spots. 

    Arrow starts from end middle of first activity and ends at the top middle of
    the next activity.
    
    Parameters
    ----------
    fig : plotly.figure
        Figure to draw the arrows on.
    first_job_dict : dict
        Dict of the starting arrow event.
        {Task:<string>,
         Start:<datetime>,
         Finish:<datetime>}
    second_job_dict : dict
        Dict of the ending arrow event. Same type as first_job_dict.
    color : string (Optional)
        Color of line. The default is "blue".
    width : int (Optional)
        Line width. The default is 2.
        
    Returns
    -------
    fig : plotly.figure
        New figure with arrow drawn.

    '''
    ## retrieve tick text and tick vals
    job_yaxis_mapping = dict(zip(fig.layout.yaxis.ticktext,fig.layout.yaxis.tickvals))
    jobs_x_delta      = second_job_dict['Finish'] - second_job_dict['Start']
    jobs_y_delta      = job_yaxis_mapping[first_job_dict['Task']] - job_yaxis_mapping[second_job_dict['Task']]
    
    ## horizontal line segment
    fig = draw_line(fig=fig,
                    x0=first_job_dict['Finish'],
                    x1=second_job_dict['Finish'] - jobs_x_delta/2,
                    y0=job_yaxis_mapping[first_job_dict['Task']],
                    y1=job_yaxis_mapping[first_job_dict['Task']],
                    color=color,
                    width=width
    )
    
    ## vertical line segment
    if jobs_y_delta < 0:
        fig = draw_line(fig=fig,
                        x0=second_job_dict['Finish'] - jobs_x_delta/2,
                        x1=second_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']],
                        y1=job_yaxis_mapping[second_job_dict['Task']] - 1/2,
                        color=color,
                        width=width
        )
        ## draw an arrow
        fig.add_annotation(
            x=second_job_dict['Finish'] - jobs_x_delta/2,
            y=job_yaxis_mapping[second_job_dict['Task']] - 1/2,
            xref="x",yref="y",
            showarrow=True,
            ax=0,
            ay=-13,
            ayref='pixel',
            arrowwidth=2,
            arrowcolor=color,
            arrowhead=2,
        )
        
    elif jobs_y_delta >= 0:
        fig = draw_line(fig=fig,
                        x0=second_job_dict['Finish'] - jobs_x_delta/2,
                        x1=second_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']],
                        y1=job_yaxis_mapping[second_job_dict['Task']] + 1/2,
                        color=color,
                        width=width
        )       

        ## draw an arrow
        fig.add_annotation(
            x=second_job_dict['Finish'] - jobs_x_delta/2,
            y=job_yaxis_mapping[second_job_dict['Task']] + 1/2,
            xref="x",yref="y",
            showarrow=True,
            ax=0,
            ay=13,
            ayref='pixel',
            arrowwidth=2,
            arrowcolor=color,
            arrowhead=2,
        )

    return fig

def draw_arrow_between_jobs_v2(fig, first_job_dict, second_job_dict, color="blue", width=2):
    '''
    Draws an arrow between two gantt activities on a plotly chart. Requires timing
    of the activity to determine start and end spots. 
    
    Arrow starts from middle bottom of first activity and ends at the beginning of
    the next activity.

    Parameters
    ----------
    fig : plotly.figure
        Figure to draw the arrows on.
    first_job_dict : dict
        Dict of the starting arrow event.
        {Task:<string>,
         Start:<datetime>,
         Finish:<datetime>}
    second_job_dict : dict
        Dict of the ending arrow event. Same type as first_job_dict.
    color : string (Optional)
        Color of line. The default is "blue".
    width : int (Optional)
        Line width. The default is 2.
    
    Returns
    -------
    fig : plotly.figure
        New figure with arrow drawn.

    '''
    ## retrieve tick text and tick vals
    job_yaxis_mapping = dict(zip(fig.layout.yaxis.ticktext,fig.layout.yaxis.tickvals))
    jobs_x_delta      = first_job_dict['Finish'] - first_job_dict['Start']
    jobs_y_delta      = job_yaxis_mapping[first_job_dict['Task']] - job_yaxis_mapping[second_job_dict['Task']]

    ## vertical line segment
    if jobs_y_delta < 0:
        fig = draw_line(fig=fig,
                        x0=first_job_dict['Finish'] - jobs_x_delta/2,
                        x1=first_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']] + 1/2,
                        y1=job_yaxis_mapping[second_job_dict['Task']],
                        color=color,
                        width=width
        )
    elif jobs_y_delta >= 0:
        fig = draw_line(fig=fig,
                        x0=first_job_dict['Finish'] - jobs_x_delta/2,
                        x1=first_job_dict['Finish'] - jobs_x_delta/2,
                        y0=job_yaxis_mapping[first_job_dict['Task']] - 1/2,
                        y1=job_yaxis_mapping[second_job_dict['Task']],
                        color=color,
                        width=width
        )        

    ## horizontal line segment
    fig = draw_line(fig=fig,
                    x0=first_job_dict['Finish'] - jobs_x_delta/2,
                    x1=second_job_dict['Start'],
                    y0=job_yaxis_mapping[second_job_dict['Task']],
                    y1=job_yaxis_mapping[second_job_dict['Task']],
                    color=color,
                    width=width
    )

    ## draw an arrow
    fig.add_annotation(
        x=second_job_dict['Start'], 
        y=job_yaxis_mapping[second_job_dict['Task']],
        xref="x",yref="y",
        showarrow=True,
        ax=-10,
        ay=0,
        arrowwidth=2,
        arrowcolor=color,
        arrowhead=2,
    )
    return fig

def draw_line(fig, x0, x1, y0, y1, color="blue", width=2):
    '''
    Draws a line between 2 coordinate points.

    Parameters
    ----------
    fig : plotly figure
        Figure to draw line on.
    x0 : object
        Start x coordinate.
    x1 : object
        End x coordinate.
    y0 : object
        Start y coordinate.
    y1 : object
        End y coordinate.
    color : string (Optional)
        Color of line. The default is "blue".
    width : int (Optional)
        Line width. The default is 2.

    Returns
    -------
    fig : plotly figure
        Figure with lines

    '''
    ## horizontal line segment
    fig.add_shape(
        x0=x0, y0=y0, 
        x1=x1, y1=y1,
        line=dict(color=color, width=width)
    )
    
    return fig     

def get_data(ret_pd=False):
    all_records = app_tables.json_table.search(tables.order_by("Start"))
    input       = [{'Task':r['Task'],
                  'Start':r['Start'],
                  'Finish':r['Finish'],
                  'Duration':r['Duration'],
                  'Adj':r['Adj'],
                  'Resource':r["Resource"],
                  'CP_flag':r['CP_flag'],
                  'Group':r['Group']
                 } for r in all_records] 
    if ret_pd:
      return pd.DataFrame.from_dict(input)
    else:
      return input

def format_data(pd_data):
  pd_data["Start"]      = pd.to_datetime(pd_data["Start"]).dt.date
  pd_data["Finish"]     = pd.to_datetime(pd_data["Finish"]).dt.date
  pd_data["Duration"]   = pd.to_numeric(pd_data["Duration"])

  return pd_data
  
def is_critical(pd_data):
    if pd_data["CP_flag"]:
        return "Is Critical"
    else:
        return "Not Critical"

def relabel(json_list):
    group_tag = json_list["Group"]
    dest_Adj = json_list["Adj"]
    newlist = list()
    for line in dest_Adj:
        newlist.append(line + "_" + group_tag)
    
    return newlist
  
def update_resource_table():
  all_records = app_tables.json_table.search() 
  temp        = [{'Resource':r["Resource"]
                 } for r in all_records] 
  data        = pd.DataFrame.from_dict(temp)

  # Find unique instances 
  df = data["Resource"].apply(pd.Series).stack().unique()

  # Remove existing resource table and recreate
  app_tables.resource_table.delete_all_rows()
  for name in df:
    app_tables.resource_table.add_row(resource_name=name,
                                      resource_description="placeholder"
                                     )

def update_group_table():
  all_records = app_tables.json_table.search() 
  temp        = [{'Group':r['Group']
                 } for r in all_records] 
  data        = pd.DataFrame.from_dict(temp)

  df = data["Group"].apply(pd.Series).stack().unique()
  app_tables.group_table.delete_all_rows()
  for name in df:
    app_tables.group_table.add_row(group_name=name,
                                   group_description="placeholder"
                                     )
################################################ END SCHEDULE ###################################################

