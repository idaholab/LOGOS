# -*- coding: utf-8 -*-
"""
Created on Tue Jul 30 09:02:49 2024

@author: Edward Chen
@email: edward.chen@inl.gov
"""

def draw_task_links(fig, json_dict):
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
            CP_flag = end_act["CP_flag"] and start_act["CP_flag"]
            if not CP_flag:
                new_fig = draw_arrow_between_jobs_v2(new_fig, start_act, end_act)
        
        # Draw critical path on top of existing links
        for endPoint in link[1]["Adj"]:
            ind     = json_dict.index[json_dict["Task"]==endPoint].tolist()
            end_act = json_dict.loc[ind[0]] 
            CP_flag = end_act["CP_flag"] and start_act["CP_flag"]
            
            if CP_flag:
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