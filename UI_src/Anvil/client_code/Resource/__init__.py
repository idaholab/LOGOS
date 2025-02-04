from ._anvil_designer import ResourceTemplate
from anvil import *
import anvil.server
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables

from .AddResource import AddResource

class Resource(ResourceTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Populate displayed resource table
    self.__update_table__()

    # Set event handlers
    self.set_event_handler('x-update-resources', self.__update_table__)
  
  def __update_table__(self, **event_args):
    self.repeating_panel_1.items = get_open_form().resource_table

  def add_button_click(self, **event_args):
    '''
    Add a new resource when the "Add Resource" button is pressed.
    '''
    adding_form = AddResource(item=self.item)
    result = alert(content=adding_form, large=True, 
                   buttons=[("Accept", True),
                            ("Cancel", False)
                           ])
    if result:      
      anvil.server.call('add_resource', adding_form.resource_name.text, adding_form.resource_description.text)
      get_open_form().raise_event('x-refresh-tables')
      self.__update_table__()
      return
    else:
      alert("Error, could not add resource to table", title="Error")
      return

  def refresh_button_click(self, **event_args):
     self.__update_table__()
    
    
    
