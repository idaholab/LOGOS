from ._anvil_designer import MainTemplate
from anvil import *
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
import anvil.server

from ..Home import Home
from ..Schedule import Schedule
from ..Activity import Activity
from ..Resource import Resource
from ..Contact import Contact
from ..Group import Group

class Main(MainTemplate):
  def __init__(self, **properties):
    # Set Form properties and Data Bindings.
    self.init_components(**properties)

    # Initiate default homepage panel 
    self.content_panel.clear()
    self.content_panel.add_component(Home(), full_width_row=True)

    # Pre-load existing data tables
    self.update_tables()
    self.set_event_handler('x-refresh-tables', self.update_tables)
    
    # Adding global parameters
    self.logo_image.source = "_/theme/logo.PNG" # Add LOGOS logo to page
    self.start_date_selected = None
    self.end_date_selected = None
    self.fig = None
    
  def home_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Home(), full_width_row=True)

  def schedule_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Schedule(), full_width_row=True)

  def resource_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Resource(), full_width_row=True)

  def activity_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Activity(), full_width_row=True)

  def contact_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Contact(), full_width_row=True)

  def group_button_click(self, **event_args):
    """This method is called when the button is clicked"""
    self.content_panel.clear()
    self.content_panel.add_component(Group(), full_width_row=True)
    
  def update_tables(self, **event_args):
    resource_table, activity_table, json_table, increment, group_table = anvil.server.call('get_all_tables')
    self.resource_table = resource_table
    self.activity_table = activity_table
    self.json_table     = json_table
    self.increment      = increment
    self.group_table    = group_table
    print("Updating")





  


    

  







