# -*- coding: utf-8 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

"""Build master implementation."""

import calendar
from datetime import datetime, timedelta
import logging
import os
import re
try:
    set
except NameError:
    from sets import Set as set
import sys
import time

from trac.config import BoolOption, IntOption
from trac.core import *
from trac.env import Environment
from trac.web import IRequestHandler, HTTPMethodNotAllowed, HTTPNotFound, \
                     RequestDone

from bitten.model import BuildConfig, Build, BuildStep, BuildLog, Report
from bitten.queue import BuildQueue
from bitten.trac_ext.main import BuildSystem
from bitten.util import xmlio

log = logging.getLogger('bitten.master')


class BuildMaster(Component):
    """BEEP listener implementation for the build master."""

    implements(IRequestHandler)

    # Configuration options

    adjust_timestamps = BoolOption('bitten', 'adjust_timestamps', False, doc=
        """Whether the timestamps of builds should be adjusted to be close '
        to the timestamps of the corresponding changesets.""")

    build_all = BoolOption('bitten', 'build_all', False, doc=
        """Whether to request builds of older revisions even if a younger
        revision has already been built.""")

    slave_timeout = IntOption('bitten', 'slave_timeout', 3600, doc=
        """The time in seconds after which a build is cancelled if the slave
        does not report progress.""")

    # Initialization

    def __init__(self):
        self.queue = BuildQueue(self.env, build_all=self.build_all)

    # IRequestHandler methods

    def match_request(self, req):
        match = re.match(r'/builds(?:/(\d+)(?:/(\w+)/([^/]+))?)?$', req.path_info)
        if match:
            if match.group(1):
                req.args['id'] = match.group(1)
                req.args['collection'] = match.group(2)
                req.args['member'] = match.group(3)
            return True

    def process_request(self, req):
        req.perm.assert_permission('BUILD_EXEC')

        if 'id' not in req.args:
            if req.method != 'POST':
                raise HTTPMethodNotAllowed('Method not allowed')
            self.queue.populate()
            return self._process_build_creation(req)

        build = Build.fetch(self.env, req.args['id'])
        if not build:
            raise HTTPNotFound('No such build')
        config = BuildConfig.fetch(self.env, build.config)

        if not req.args['collection']:
            return self._process_build_initiation(req, config, build)
        elif req.args['collection'] == 'steps':
            return self._process_build_step(build, config, build,
                                            req.args['member'])
        elif req.args['collection'] == 'files':
            return self._process_build_artifact(build, config, build,
                                                req.args['member'])

    def _process_build_creation(self, req):
        body = req.read()
        elem = xmlio.parse(body)

        info = {'name': elem.attr['name'], Build.IP_ADDRESS: req.remote_addr}
        for child in elem.children():
            if child.name == 'platform':
                info[Build.MACHINE] = child.gettext()
                info[Build.PROCESSOR] = child.attr.get('processor')
            elif child.name == 'os':
                info[Build.OS_NAME] = child.gettext()
                info[Build.OS_FAMILY] = child.attr.get('family')
                info[Build.OS_VERSION] = child.attr.get('version')
            elif child.name == 'package':
                for name, value in child.attr.items():
                    if name == 'name':
                        continue
                    info[child.attr['name'] + '.' + name] = value

        if not self.queue.register_slave(info['name'], info):
            req.send('No pending builds', 'text/plain', 204)

        # FIXME: this API should be changed, we no longer need to pass multiple
        #        slave names in, and get a selected slave name back
        build, slave = self.queue.get_next_pending_build([info['name']])
        build.slave = info['name']
        build.slave_info.update(info)
        build.status = Build.IN_PROGRESS
        build.update()

        req.send_header('Location', req.abs_href.builds(build.id))
        req.send('Build pending', 'text/plain', 201)

    def _process_build_initiation(self, req, config, build):
        build.started = int(time.time())
        build.update()

        req.send_header('Content-Disposition',
                        'attachment; filename=recipe_%s_r%s.xml' %
                        (config.name, build.rev))

        xml = xmlio.parse(config.recipe)
        xml.attr['path'] = config.path
        xml.attr['revision'] = build.rev
        req.send(str(xml), 'application/x-bitten+xml', 200)

    def _process_build_step(self, req, config, build, stepname):
        raise NotImplementedError

    def _process_build_artifact(self, req, config, build, filename):
        raise NotImplementedError
