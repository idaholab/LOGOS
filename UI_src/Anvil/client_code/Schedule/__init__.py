from ._anvil_designer import ScheduleTemplate
from anvil import *
import anvil.server
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables

from datetime import datetime 
import json

class Schedule(ScheduleTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Populate interval option dropdown (never changes)
    self.interval_drop_down.items = [(row["increment_value"]) for row in get_open_form().increment]
    self.interval_drop_down.selected_value = self.interval_drop_down.items[0] # Default to 'days'

    # Populate other option dropdown menus 
    self.__refresh_self__()

    # Show figure if one has alrady been generated
    if not (get_open_form().fig == None):
      self.plot_1.figure = get_open_form().fig
      
  def import_button_change(self, file, **event_args):
    # Load JSON file and create associated table from data
    success = anvil.server.call("load_file", file)
    get_open_form().raise_event('x-refresh-tables')
    self.__refresh_self__()
    if success:
      self.__refreshPlot__()
    else:
      anvil.alert("Import unsuccessful, file type unsupported. Gantt chart data must be JSON formatted file.")

  def refresh_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.__refreshPlot__()

  def __refreshPlot__(self, **event_args):
    if self.CP_flag:
      pass

    if self.group_dropdown.selected_value == None:
      grouping = "All"
    else:
      grouping = self.group_dropdown.selected_value
      
    if self.simplified_flag.checked:
      fig = anvil.server.call("draw_simplified_chart", 
                              start_date=self.start_datePicker.date, 
                              end_date=self.end_datePicker.date, 
                              interval=self.interval_drop_down.selected_value,
                              showCrit=self.CP_flag.checked,
                              showArrow=self.ArrowOpt.checked,
                              Group = grouping
                             )
    else:
      print(self.ArrowOpt.checked)
      fig = anvil.server.call("draw_full_chart", 
                              start_date=self.start_datePicker.date, 
                              end_date=self.end_datePicker.date, 
                              interval=self.interval_drop_down.selected_value,
                              showCrit=self.CP_flag.checked,
                              showArrow=self.ArrowOpt.checked,
                              Group = grouping
                             )

    get_open_form().fig = fig
    self.plot_1.figure = fig

  def __refresh_self__(self, **event_args):
    self.group_dropdown.items = [(row["group_name"]) for row in get_open_form().group_table]

  


    

