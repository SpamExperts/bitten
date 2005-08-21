# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
#
# Bitten is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Trac is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
#
# Author: Christopher Lenz <cmlenz@gmx.de>

import unittest

from bitten.tests import model, recipe, store
from bitten.build import tests as build
from bitten.util import tests as util
from bitten.trac_ext import tests as trac_ext

def suite():
    suite = unittest.TestSuite()
    suite.addTest(model.suite())
    suite.addTest(recipe.suite())
    suite.addTest(store.suite())
    suite.addTest(build.suite())
    suite.addTest(trac_ext.suite())
    suite.addTest(util.suite())
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')