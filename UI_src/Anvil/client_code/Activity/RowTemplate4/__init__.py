from ._anvil_designer import RowTemplate4Template
from anvil import *
import anvil.server
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables

class RowTemplate4(RowTemplate4Template):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)
    
    self.Group_edit.items = [(row["group_name"]) for row in get_open_form().group_table]

  def edit_button_click(self, **event_args):
    self.Task_resource_edit.items =  [(row["resource_name"]) for row in get_open_form().resource_table]
    self.Task_dependency_edit.items = [(row["Task"]) for row in get_open_form().json_table]

    self.Task_resource_edit.reset()
    self.Task_dependency_edit.reset()
    
    self.Task_resource_edit.add_to_token(self.item['Resource'])
    self.Task_dependency_edit.add_to_token(self.item['Adj'])

    """This method is called when the edit button is clicked"""
    self.data_row_panel_1.visible=False
    self.data_row_panel_2.visible=True

  def save_button_click(self, **event_args):
    """This method is called when the save button is clicked"""
    self.data_row_panel_1.visible=True
    self.data_row_panel_2.visible=False

    self.edit_activity()

  def delete_button_click(self, **event_args):
    """This method is called when the delete button is clicked"""
    anvil.server.call('delete_activity', self.item)
    
  def edit_activity(self):    
    task_name        = self.Task_name_edit.text
    task_description = self.Task_decription_edit.text
    task_start       = self.Start_edit.date
    task_end         = self.End_edit.date
    task_resource    = self.Task_resource_edit.selected
    task_adj         = self.Task_dependency_edit.selected
    task_group       = self.Group_edit.selected_value
    task_CP          = self.CP_flag_edit.checked

    anvil.server.call('edit_activity',
                      self.item,
                      Task=task_name,
                      Description=task_description,
                      Start=task_start,
                      Finish=task_end,
                      Resource=task_resource,
                      Adj=task_adj,
                      Group=task_group,
                      CP_flag=task_CP
                    )
    get_open_form().raise_event('x-refresh-tables')
    self.refresh_data_bindings()

    



  



  

  
