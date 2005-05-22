from distutils.core import setup, Command
from bitten.distutils.testrunner import unittest

setup(name='bitten', version='1.0',
      packages=['bitten', 'bitten.general', 'bitten.python'],
      cmdclass={'unittest': unittest})
