from ._anvil_designer import TokenBoxTemplate
from anvil import *
import anvil.server

class TokenBox(TokenBoxTemplate):
  def __init__(self, **properties):
    # You must call self.init_components() before doing anything else in this function
    self.init_components(**properties)
    
    self.selected = list()
    
  def add(self, text):
    """Add a token to the Flow Panel (and call the add_callback)."""
    self.selected.append(text)
    token = Button(
      text=text,
      icon="fa:times",
      icon_align="left",
      role="primary-color",
    )
    token.set_event_handler("click", self.remove)
    self.flow_panel_1.add_component(token)

    if callable(self.add_callback):
      # You can register a callback to be called after a token has been added.
      self.add_callback(token)

  def remove(self, **event_args):
    """Remove a token from the Flow Panel (and call the remove_callback)."""
    token = event_args['sender']
    remove_item = token.text
    self.selected.remove(remove_item)
    
    if callable(self.remove_callback):
      # You can register a callback to be called before a token is removed.
      self.remove_callback(token)
    token.remove_from_parent()
