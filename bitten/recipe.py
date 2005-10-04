# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import keyword
import logging
import os
import re
try:
    set
except NameError:
    from sets import Set as set

from pkg_resources import WorkingSet
from bitten.build import BuildError
from bitten.build.config import Configuration
from bitten.util import xmlio

__all__ = ['Recipe']

log = logging.getLogger('bitten.recipe')


class InvalidRecipeError(Exception):
    """Exception raised when a recipe cannot be processed."""


class Context(object):
    """The context in which a recipe command or report is run."""

    step = None # The current step
    generator = None # The current generator (namespace#name)

    def __init__(self, basedir, config=None):
        self.basedir = os.path.realpath(basedir)
        self.config = config or Configuration()
        self.output = []

    def run(self, step, namespace, name, attr):
        if not namespace:
            log.warn('Ignoring element <%s> without namespace', name)
            return

        group = 'bitten.recipe_commands'
        qname = namespace + '#' + name
        function = None
        for entry_point in WorkingSet().iter_entry_points(group, qname):
            function = entry_point.load()
            break
        else:
            raise InvalidRecipeError, 'Unknown recipe command %s' % qname

        def escape(name):
            name = name.replace('-', '_')
            if keyword.iskeyword(name) or name in __builtins__:
                name = name + '_'
            return name
        args = dict([(escape(name), self.config.interpolate(attr[name]))
                     for name in attr])

        self.step = step
        self.generator = qname
        log.debug('Executing %s with arguments: %s', function, args)
        function(self, **args)
        self.generator = None
        self.step = None

    def error(self, message):
        self.output.append((Recipe.ERROR, None, self.generator, message))

    def log(self, xml_elem):
        self.output.append((Recipe.LOG, None, self.generator, xml_elem))

    def report(self, category, xml_elem):
        self.output.append((Recipe.REPORT, category, self.generator, xml_elem))

    def resolve(self, *path):
        return os.path.normpath(os.path.join(self.basedir, *path))


class Step(object):
    """Represents a single step of a build recipe.

    Iterate over an object of this class to get the commands to execute, and
    their keyword arguments.
    """

    def __init__(self, elem):
        self._elem = elem
        self.id = elem.attr['id']
        self.description = elem.attr.get('description')
        self.onerror = elem.attr.get('onerror', 'fail')

    def execute(self, ctxt):
        for child in self._elem:
            ctxt.run(self, child.namespace, child.name, child.attr)

        errors = []
        while ctxt.output:
            type, category, generator, output = ctxt.output.pop(0)
            yield type, category, generator, output
            if type == Recipe.ERROR:
                errors.append((generator, output))
        if errors:
            if self.onerror == 'fail':
                raise BuildError, 'Build step %s failed' % self.id
            log.warning('Ignoring errors in step %s (%s)', self.id,
                        ', '.join([error[1] for error in errors]))


class Recipe(object):
    """A build recipe.
    
    Iterate over this object to get the individual build steps in the order they
    have been defined in the recipe file.
    """

    ERROR = 'error'
    LOG = 'log'
    REPORT = 'report'

    def __init__(self, xml, basedir=os.getcwd(), config=None):
        assert isinstance(xml, xmlio.ParsedElement)
        self.ctxt = Context(basedir, config)
        self._root = xml

    def __iter__(self):
        """Provide an iterator over the individual steps of the recipe."""
        for child in self._root.children('step'):
            yield Step(child)

    def validate(self):
        if self._root.name != 'build':
            raise InvalidRecipeError, 'Root element must be <build>'
        steps = list(self._root.children())
        if not steps:
            raise InvalidRecipeError, 'Recipe defines no build steps'

        step_ids = set()
        for step in steps:
            if step.name != 'step':
                raise InvalidRecipeError, 'Only <step> elements allowed ' \
                                          'at top level of recipe'
            if not step.attr.get('id'):
                raise InvalidRecipeError, 'Steps must have an "id" attribute'

            if step.attr['id'] in step_ids:
                raise InvalidRecipeError, 'Duplicate step ID "%s"' \
                                          % step.attr['id']
            step_ids.add(step.attr['id'])

            cmds = list(step.children())
            if not cmds:
                raise InvalidRecipeError, 'Step "%s" has no recipe commands' \
                                          % step.attr['id']
            for cmd in cmds:
                if len(list(cmd.children())):
                    raise InvalidRecipeError, 'Recipe command <%s> has ' \
                                              'nested content' % cmd.name
