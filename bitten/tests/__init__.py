import unittest

from bitten.recipe import tests as recipe

def suite():
    suite = unittest.TestSuite()
    suite.addTest(recipe.suite())
    return suite
