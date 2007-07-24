# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

"""Implementation of the build slave."""

from datetime import datetime
import httplib2
import logging
import os
import platform
try:
    set
except NameError:
    from sets import Set as set
import shutil
import tempfile
import time

from bitten.build import BuildError
from bitten.build.config import Configuration
from bitten.recipe import Recipe, InvalidRecipeError
from bitten.util import xmlio

log = logging.getLogger('bitten.slave')


class BuildSlave(object):
    """BEEP initiator implementation for the build slave."""

    def __init__(self, url, name=None, config=None, dry_run=False,
                 work_dir=None, keep_files=False, single_build=False):
        """Create the build slave instance.
        
        @param url: The URL of the build master
        @param port: TCP port number of the build master to connect to
        @param name: The name with which this slave should identify itself
        @param config: The slave configuration
        @param dry_run: Whether the build outcome should not be reported back
            to the master
        @param work_dir: The working directory to use for build execution
        @param keep_files: Whether files and directories created for build
            execution should be kept when done
        @param single_build: Whether this slave should exit after completing a 
            single build, or continue processing builds forever
        """
        self.url = url
        if name is None:
            name = platform.node().split('.', 1)[0].lower()
        self.name = name
        self.config = Configuration(config)
        self.dry_run = dry_run
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix='bitten')
        elif not os.path.exists(work_dir):
            os.makedirs(work_dir)
        self.work_dir = work_dir
        self.keep_files = keep_files
        self.single_build = single_build
        self.client = httplib2.Http()

    def run(self):
        while True:
            self._create_build()
            time.sleep(30)

    def quit(self):
        log.info('Shutting down')

    def _create_build(self):
        xml = xmlio.Element('slave', name=self.name)[
            xmlio.Element('platform', processor=self.config['processor'])[
                self.config['machine']
            ],
            xmlio.Element('os', family=self.config['family'],
                                version=self.config['version'])[
                self.config['os']
            ],
        ]
        log.debug('Packages: %s', self.config.packages)
        for package, properties in self.config.packages.items():
            xml.append(xmlio.Element('package', name=package, **properties))

        resp, content = self.client.request(self.url, 'POST', str(xml),
                                                headers={
            'Content-Type': 'application/x-bitten+xml'
        })
        status = int(resp['status'])
        if status == 201:
            self._initiate_build(resp['location'])
        elif status == 204:
            log.info(content)
        else:
            log.error('Unexpected response (%d): %s', status, content)

    def _initiate_build(self, build_url):
        log.info('Build pending: %s' % build_url.split('/')[-1])
        resp, content = self.client.request(build_url, 'GET')
        status = int(resp['status'])
        if status == 200:
            recipe = xmlio.parse(content)
            print recipe


def main():
    """Main entry point for running the build slave."""
    from bitten import __version__ as VERSION
    from optparse import OptionParser

    parser = OptionParser(usage='usage: %prog [options] url',
                          version='%%prog %s' % VERSION)
    parser.add_option('--name', action='store', dest='name',
                      help='name of this slave (defaults to host name)')
    parser.add_option('-f', '--config', action='store', dest='config',
                      metavar='FILE', help='path to configuration file')
    parser.add_option('-d', '--work-dir', action='store', dest='work_dir',
                      metavar='DIR', help='working directory for builds')
    parser.add_option('-k', '--keep-files', action='store_true',
                      dest='keep_files', 
                      help='don\'t delete files after builds')
    parser.add_option('-l', '--log', dest='logfile', metavar='FILENAME',
                      help='write log messages to FILENAME')
    parser.add_option('-n', '--dry-run', action='store_true', dest='dry_run',
                      help='don\'t report results back to master')
    parser.add_option('--debug', action='store_const', dest='loglevel',
                      const=logging.DEBUG, help='enable debugging output')
    parser.add_option('-v', '--verbose', action='store_const', dest='loglevel',
                      const=logging.INFO, help='print as much as possible')
    parser.add_option('-q', '--quiet', action='store_const', dest='loglevel',
                      const=logging.ERROR, help='print as little as possible')
    parser.add_option('-s', '--single', action='store_true',
                      dest='single_build',
                      help='exit after completing a single build')
    parser.set_defaults(dry_run=False, keep_files=False,
                        loglevel=logging.WARNING, single_build=False)
    options, args = parser.parse_args()

    if len(args) < 1:
        parser.error('incorrect number of arguments')
    url = args[0]

    logger = logging.getLogger('bitten')
    logger.setLevel(options.loglevel)
    handler = logging.StreamHandler()
    handler.setLevel(options.loglevel)
    formatter = logging.Formatter('[%(levelname)-8s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if options.logfile:
        handler = logging.FileHandler(options.logfile)
        handler.setLevel(options.loglevel)
        formatter = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: '
                                      '%(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    slave = BuildSlave(url, name=options.name, config=options.config,
                       dry_run=options.dry_run, work_dir=options.work_dir,
                       keep_files=options.keep_files,
                       single_build=options.single_build)
    try:
        slave.run()
    except KeyboardInterrupt:
        slave.quit()

    if not options.keep_files and os.path.isdir(slave.work_dir):
        shutil.rmtree(slave.work_dir)

if __name__ == '__main__':
    main()
