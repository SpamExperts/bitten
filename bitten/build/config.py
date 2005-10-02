# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

from ConfigParser import SafeConfigParser
import os
import platform
import re


class Configuration(object):
    """Encapsulates the configuration of a build machine.
    
    Configuration values can be provided through a configuration file (in INI
    format) or through command-line parameters (properties). In addition to
    explicitly defined properties, this class automatically collects platform
    information and stores them as properties. These defaults can be
    overridden (useful for cross-compilation).
    """
    # TODO: document mapping from config file to property names

    def __init__(self, filename=None, properties=None):
        """Create the configuration object.
        
        @param filename: The path to the configuration file, if any
        @param properties: A dictionary of the configuration properties
                           provided on the command-line
        """
        self.properties = {}
        self.packages = {}
        parser = SafeConfigParser()
        if filename:
            parser.read(filename)
        self._merge_sysinfo(parser, properties)
        self._merge_packages(parser, properties)

    def _merge_sysinfo(self, parser, properties):
        """Merge the platform information properties into the configuration."""
        system, node, release, version, machine, processor = platform.uname()
        system, release, version = platform.system_alias(system, release,
                                                         version)
        self.properties['machine'] = machine
        self.properties['processor'] = processor
        self.properties['os'] = system
        self.properties['family'] = os.name
        self.properties['version'] = release

        mapping = {'machine': ('machine', 'name'),
                   'processor': ('machine', 'processor'),
                   'os': ('os', 'name'),
                   'family': ('os', 'family'),
                   'version': ('os', 'version')}
        for key, (section, option) in mapping.items():
            if parser.has_section(section):
                value = parser.get(section, option)
                if value is not None:
                    self.properties[key] = value

        if properties:
            for key, value in properties.items():
                if key in mapping:
                    self.properties[key] = value

    def _merge_packages(self, parser, properties):
        """Merge package information into the configuration."""
        for section in parser.sections():
            if section in ('os', 'machine', 'maintainer'):
                continue
            package = {}
            for option in parser.options(section):
                package[option] = parser.get(section, option)
            self.packages[section] = package

        if properties:
            for key, value in properties.items():
                if '.' in key:
                    package, propname = key.split('.', 1)
                    if package not in self.packages:
                        self.packages[package] = {}
                    self.packages[package][propname] = value

    def __contains__(self, key):
        if '.' in key:
            package, propname = key.split('.', 1)
            return propname in self.packages.get(package, {})
        return key in self.properties

    def __getitem__(self, key):
        if '.' in key:
            package, propname = key.split('.', 1)
            return self.packages.get(package, {}).get(propname)
        return self.properties.get(key)

    def __str__(self):
        return str({'properties': self.properties, 'packages': self.packages})

    _VAR_RE = re.compile(r'\$\{(?P<ref>\w[\w.]*?\w)(?:\:(?P<def>.+))?\}')

    def interpolate(self, text):
        """Interpolate configuration properties into a string.
        
        Properties can be referenced in the text using the notation
        `${property.name}`. A default value can be provided by appending it to
        the property name separated by a colon, for example
        `${property.name:defaultvalue}`. This value will be used when there's
        no such property in the configuration. Otherwise, if no default is
        provided, the reference is not replaced at all.
        """
        def _replace(m):
            refname = m.group('ref')
            if refname in self:
                return self[refname]
            elif m.group('def'):
                return m.group('def')
            else:
                return m.group(0)
        return self._VAR_RE.sub(_replace, text)