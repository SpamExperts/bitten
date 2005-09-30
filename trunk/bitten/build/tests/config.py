# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import platform
import os
import shutil
import tempfile
import unittest

from bitten.build.config import Configuration


class ConfigurationTestCase(unittest.TestCase):

    def test_sysinfo_defaults(self):
        config = Configuration()

        self.assertEqual(platform.machine(), config['machine'])
        self.assertEqual(platform.processor(), config['processor'])
        system, release, version = platform.system_alias(platform.system(),
                                                         platform.release(),
                                                         platform.version())
        self.assertEqual(system, config['os'])
        self.assertEqual(os.name, config['family'])
        self.assertEqual(release, config['version'])

    def test_sysinfo_properties_override(self):
        config = Configuration(properties={
            'machine': 'MACHINE',
            'processor': 'PROCESSOR',
            'os': 'OS',
            'family': 'FAMILY',
            'version': 'VERSION'
        })
        self.assertEqual('MACHINE', config['machine'])
        self.assertEqual('PROCESSOR', config['processor'])
        self.assertEqual('OS', config['os'])
        self.assertEqual('FAMILY', config['family'])
        self.assertEqual('VERSION', config['version'])

    def test_sysinfo_configfile_override(self):
        inifile, ininame = tempfile.mkstemp(prefix='bitten_test')
        try:
            os.write(inifile, """
[machine]
name = MACHINE
processor = PROCESSOR

[os]
name = OS
family = FAMILY
version = VERSION
""")
            os.close(inifile)
            config = Configuration(ininame)

            self.assertEqual('MACHINE', config['machine'])
            self.assertEqual('PROCESSOR', config['processor'])
            self.assertEqual('OS', config['os'])
            self.assertEqual('FAMILY', config['family'])
            self.assertEqual('VERSION', config['version'])
        finally:
            os.remove(ininame)

    def test_package_properties(self):
        config = Configuration(properties={
            'python.version': '2.3.5',
            'python.path': '/usr/local/bin/python2.3'
        })
        self.assertEqual(True, 'python' in config.packages)
        self.assertEqual('/usr/local/bin/python2.3', config['python.path'])
        self.assertEqual('2.3.5', config['python.version'])

    def test_package_configfile(self):
        inifile, ininame = tempfile.mkstemp(prefix='bitten_test')
        try:
            os.write(inifile, """
[python]
path = /usr/local/bin/python2.3
version = 2.3.5
""")
            os.close(inifile)
            config = Configuration(ininame)

            self.assertEqual(True, 'python' in config.packages)
            self.assertEqual('/usr/local/bin/python2.3', config['python.path'])
            self.assertEqual('2.3.5', config['python.version'])
        finally:
            os.remove(ininame)

    def test_interpolate(self):
        config = Configuration(properties={
            'python.version': '2.3.5',
            'python.path': '/usr/local/bin/python2.3'
        })
        self.assertEqual('/usr/local/bin/python2.3',
                         config.interpolate('${python.path}'))
        self.assertEqual('foo /usr/local/bin/python2.3 bar',
                         config.interpolate('foo ${python.path} bar'))

    def test_interpolate_default(self):
        config = Configuration()
        self.assertEqual('python2.3',
                         config.interpolate('${python.path:python2.3}'))
        self.assertEqual('foo python2.3 bar',
                         config.interpolate('foo ${python.path:python2.3} bar'))

    def test_interpolate_missing(self):
        config = Configuration()
        self.assertEqual('${python.path}',
                         config.interpolate('${python.path}'))
        self.assertEqual('foo ${python.path} bar',
                         config.interpolate('foo ${python.path} bar'))


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(ConfigurationTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
