# Copyright 2020, Battelle Energy Alliance, LLC
# ALL RIGHTS RESERVED
"""
  Created on Nov. 5, 2026
  @author: wangc

  Python 3.12 removed the `imp` module. LOGOS's stochastic programming models
  depend on pyutilib (via its vendored PySP in src/contrib/pysp), which still
  does `import imp` at module scope and has no release that supports Python
  3.12. This installs a minimal `imp` replacement into sys.modules, covering
  only the entry points pyutilib actually uses, so those imports succeed.
"""
import sys
import types
import io
import importlib.util
import importlib.machinery

def ensureImpModule():
  """
    Install a minimal `imp` module shim into sys.modules if the real module is
    unavailable (Python >= 3.12). Must be called before importing pyutilib or
    LOGOS's vendored PySP (src/contrib/pysp), both of which do `import imp`.
    @ In, None
    @ Out, None
  """
  if 'imp' in sys.modules:
    return
  try:
    import imp  # noqa: F401
    return
  except ModuleNotFoundError:
    pass

  def loadSource(name, pathname, file=None):
    spec = importlib.util.spec_from_file_location(name, pathname)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

  def findModule(name, path=None):
    spec = importlib.machinery.PathFinder().find_spec(name, path)
    if spec is None or spec.origin is None:
      raise ImportError('No module named {!r}'.format(name))
    return (io.StringIO(), spec.origin, ('.py', 'r', 1))

  def loadModule(name, file, pathname, description):
    return loadSource(name, pathname)

  shim = types.ModuleType('imp')
  shim.find_module = findModule
  shim.load_module = loadModule
  shim.load_source = loadSource
  sys.modules['imp'] = shim
