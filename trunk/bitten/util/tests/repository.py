# -*- coding: utf-8 -*-
#
# Copyright (C) 2006-2013 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://trac.edgewall.org/wiki/TracLicense.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://trac.edgewall.org/log/.

import unittest
from trac.test import Mock

from bitten.util.repository import display_rev


class DisplayRevTestCase(unittest.TestCase):

    def test_using_display(self):
        repos = Mock(display_rev=lambda x: '123',
                     normalize_rev=lambda x: '123456')
        rev = '1234567890'
        self.assertEquals('123', display_rev(repos, rev))

    def test_using_normalize(self):
        repos = Mock(normalize_rev=lambda x: '123456')
        rev = '1234567890'
        self.assertEquals('123456', display_rev(repos, rev))


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(DisplayRevTestCase))
    return suite


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
