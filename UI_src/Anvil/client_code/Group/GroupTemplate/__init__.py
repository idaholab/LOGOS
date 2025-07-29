from ._anvil_designer import GroupTemplateTemplate
from anvil import *
import anvil.server
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables

from ..EditGroup import EditGroup

class GroupTemplate(GroupTemplateTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

  def edit_link_click(self, **event_args):
    """This method is called when the link is clicked"""
    editing_form = EditGroup(item=self.item)
    result = alert(content=editing_form, large=True, 
                   buttons=[("Accept", True),
                            ("Cancel", False)
                           ])

    if result:
      anvil.server.call('edit_group', self.item, editing_form.edit_group_name.text, editing_form.edit_group_description.text)
      get_open_form().raise_event('x-refresh-tables')
      self.refresh_data_bindings()
      # Bug, edited table entries do not manually update after accepting new edits. Manual refresh required.
      return
    else:
      alert("Error, could not edit group to table", title="Error")
      return
      
  def delete_link_click(self, **event_args):
    """This method is called when the link is clicked"""
    anvil.server.call("delete_group", self.item) 
    get_open_form().raise_event('x-refresh-tables')
    self.remove_from_parent()

