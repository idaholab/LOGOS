#
#
#

"""
Script to generate the installer
"""

import sys
import os
from setuptools import setup
from setuptools import find_packages

long_description = '''
Optimization Packages for RIAM and PHM
'''
AUTHOR = 'RAVEN Developers'
AUTHOR_EMAIL = 'raven-devel@inl.gov'

setup(
    name='CapitalInvestments',
    version='0.0.1',
    author=AUTHOR,
    author_email=AUTHOR_EMAIL,
    license='',
    description='A module for risk informed asset management',
    long_description=long_description,
    install_requires=[
        'numpy',
        'pandas',
        'pyomo',
        'glpk',
        ],
    extras_require={
        "cbc": ["cbc"],
        "ipopt": ["ipopt"],
    },
    packages=find_packages()
)
