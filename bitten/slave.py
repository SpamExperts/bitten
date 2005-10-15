# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

from ConfigParser import ConfigParser
from datetime import datetime
import logging
import os
import platform
try:
    set
except NameError:
    from sets import Set as set
import shutil
import tempfile
import zipfile

from bitten.build import BuildError
from bitten.build.config import Configuration
from bitten.recipe import Recipe, InvalidRecipeError
from bitten.util import beep, xmlio

log = logging.getLogger('bitten.slave')


class Slave(beep.Initiator):
    """Build slave."""

    def __init__(self, ip, port, name=None, config=None, dry_run=False,
                 work_dir=None, keep_files=False):
        beep.Initiator.__init__(self, ip, port)
        self.name = name
        self.config = config
        self.dry_run = dry_run
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix='bitten')
        elif not os.path.exists(work_dir):
            os.makedirs(work_dir)
        self.work_dir = work_dir
        self.keep_files = keep_files

    def greeting_received(self, profiles):
        if OrchestrationProfileHandler.URI not in profiles:
            err = 'Peer does not support the Bitten orchestration profile'
            log.error(err)
            raise beep.TerminateSession, err
        self.channels[0].profile.send_start([OrchestrationProfileHandler])


class OrchestrationProfileHandler(beep.ProfileHandler):
    """Handler for communication on the Bitten build orchestration profile from
    the perspective of the build slave.
    """
    URI = 'http://bitten.cmlenz.net/beep/orchestration'

    def handle_connect(self):
        """Register with the build master."""
        self.build_xml = None

        def handle_reply(cmd, msgno, ansno, payload):
            if cmd == 'ERR':
                if payload.content_type == beep.BEEP_XML:
                    elem = xmlio.parse(payload.body)
                    if elem.name == 'error':
                        log.error('Slave registration failed: %s (%d)',
                                  elem.gettext(), int(elem.attr['code']))
                raise beep.TerminateSession, 'Registration failed!'
            log.info('Registration successful')

        self.config = Configuration(self.session.config)
        if self.session.name is not None:
            node = self.session.name
        else:
            node = platform.node().split('.', 1)[0].lower()

        log.info('Registering with build master as %s', node)
        log.debug('Properties: %s', self.config.properties)
        xml = xmlio.Element('register', name=node)[
            xmlio.Element('platform', processor=self.config['processor'])[
                self.config['machine']
            ],
            xmlio.Element('os', family=self.config['family'],
                                version=self.config['version'])[
                self.config['os']
            ],
        ]
        for package, properties in self.config.packages.items():
            xml.append(xmlio.Element('package', name=package, **properties))

        self.channel.send_msg(beep.Payload(xml), handle_reply)

    def handle_msg(self, msgno, payload):
        if payload.content_type == beep.BEEP_XML:
            elem = xmlio.parse(payload.body)
            if elem.name == 'build':
                # Received a build request
                self.build_xml = elem
                xml = xmlio.Element('proceed')
                self.channel.send_rpy(msgno, beep.Payload(xml))

        elif payload.content_type == 'application/zip':
            # Received snapshot archive for build
            project_name = self.build_xml.attr.get('project', 'default')
            project_dir = os.path.join(self.session.work_dir, project_name)
            if not os.path.exists(project_dir):
                os.mkdir(project_dir)

            archive_name = payload.content_disposition
            if not archive_name:
                archive_name = 'snapshot.zip'
            archive_path = os.path.join(project_dir, archive_name)

            archive_file = file(archive_path, 'wb')
            try:
                shutil.copyfileobj(payload.body, archive_file)
            finally:
                archive_file.close()
            basedir = self.unpack_snapshot(msgno, project_dir, archive_name)

            try:
                recipe = Recipe(self.build_xml, basedir, self.config)
                self.execute_build(msgno, recipe)
            finally:
                if not self.session.keep_files:
                    shutil.rmtree(basedir)
                    os.remove(archive_path)

    def unpack_snapshot(self, msgno, project_dir, archive_name):
        """Unpack a snapshot archive."""
        path = os.path.join(project_dir, archive_name)
        log.debug('Received snapshot archive: %s', path)
        try:
            zip_file = zipfile.ZipFile(path, 'r')
            try:
                badfile = zip_file.testzip()
                if badfile:
                    log.error('Bad CRC for file %s in ZIP archive at %s',
                              badfile, path)
                    raise beep.ProtocolError(550, 'Corrupt snapshot archive')

                names = []
                for name in zip_file.namelist():
                    if name.startswith('/') or '..' in name:
                        continue
                    names.append(os.path.normpath(name))
                    fullpath = os.path.join(project_dir, name)
                    if name.endswith('/'):
                        os.makedirs(fullpath)
                    else:
                        dirname = os.path.dirname(fullpath)
                        if not os.path.isdir(dirname):
                            os.makedirs(dirname)
                        fileobj = file(fullpath, 'wb')
                        try:
                            fileobj.write(zip_file.read(name))
                        finally:
                            fileobj.close()
            finally:
                zip_file.close()

            basedir = os.path.join(project_dir,  os.path.commonprefix(names))
            log.debug('Unpacked snapshot to %s' % basedir)
            return basedir

        except (IOError, zipfile.error), e:
            log.error('Could not unpack archive %s: %s', path, e, exc_info=True)
            raise beep.ProtocolError(550, 'Could not unpack archive (%s)' % e)

    def execute_build(self, msgno, recipe):
        log.info('Building in directory %s', recipe.ctxt.basedir)
        try:
            if not self.session.dry_run:
                xml = xmlio.Element('started',
                                    time=datetime.utcnow().isoformat())
                self.channel.send_ans(msgno, beep.Payload(xml))

            failed = False
            for step in recipe:
                log.info('Executing build step "%s"', step.id)
                started = datetime.utcnow()
                try:
                    xml = xmlio.Element('step', id=step.id,
                                        description=step.description,
                                        time=started.isoformat())
                    step_failed = False
                    try:
                        for type, category, generator, output in \
                                step.execute(recipe.ctxt):
                            if type == Recipe.ERROR:
                                step_failed = True
                            xml.append(xmlio.Element(type, category=category,
                                                     generator=generator)[
                                output
                            ])
                    except BuildError, e:
                        log.error('Build step %s failed', step.id)
                        failed = True
                    xml.attr['duration'] = (datetime.utcnow() - started).seconds
                    if step_failed:
                        xml.attr['result'] = 'failure'
                        log.warning('Build step %s failed', step.id)
                    else:
                        xml.attr['result'] = 'success'
                        log.info('Build step %s completed successfully',
                                 step.id)
                    if not self.session.dry_run:
                        self.channel.send_ans(msgno, beep.Payload(xml))
                except InvalidRecipeError, e:
                    log.warning('Build step %s failed: %s', step.id, e)
                    duration = datetime.utcnow() - started
                    failed = True
                    xml = xmlio.Element('step', id=step.id, result='failure',
                                        description=step.description,
                                        time=started.isoformat(),
                                        duration=duration.seconds)[
                        xmlio.Element('error')[e]
                    ]
                    if not self.session.dry_run:
                        self.channel.send_ans(msgno, beep.Payload(xml))

            if failed:
                log.warning('Build failed')
            else:
                log.info('Build completed successfully')
            if not self.session.dry_run:
                xml = xmlio.Element('completed', time=datetime.utcnow().isoformat(),
                                    result=['success', 'failure'][failed])
                self.channel.send_ans(msgno, beep.Payload(xml))

                self.channel.send_nul(msgno)
            else:
                xml = xmlio.Element('error', code=550)['Dry run']
                self.channel.send_err(msgno, beep.Payload(xml))

        except InvalidRecipeError, e:
            xml = xmlio.Element('error')[e]
            self.channel.send_ans(msgno, beep.Payload(xml))
            self.channel.send_nul(msgno)

        except (KeyboardInterrupt, SystemExit), e:
            xml = xmlio.Element('aborted')['Build cancelled']
            self.channel.send_ans(msgno, beep.Payload(xml))
            self.channel.send_nul(msgno)

            raise beep.TerminateSession, 'Cancelled'


def main():
    from bitten import __version__ as VERSION
    from optparse import OptionParser

    parser = OptionParser(usage='usage: %prog [options] host [port]',
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
    parser.add_option('-n', '--dry-run', action='store_true', dest='dry_run',
                      help='don\'t report results back to master')
    parser.add_option('--debug', action='store_const', dest='loglevel',
                      const=logging.DEBUG, help='enable debugging output')
    parser.add_option('-v', '--verbose', action='store_const', dest='loglevel',
                      const=logging.INFO, help='print as much as possible')
    parser.add_option('-q', '--quiet', action='store_const', dest='loglevel',
                      const=logging.ERROR, help='print as little as possible')
    parser.set_defaults(dry_run=False, keep_files=False,
                        loglevel=logging.WARNING)
    options, args = parser.parse_args()

    if len(args) < 1:
        parser.error('incorrect number of arguments')
    host = args[0]
    if len(args) > 1:
        try:
            port = int(args[1])
            assert (1 <= port <= 65535), 'port number out of range'
        except (AssertionError, ValueError):
            parser.error('port must be an integer in the range 1-65535')
    else:
        port = 7633

    logger = logging.getLogger('bitten')
    logger.setLevel(options.loglevel)
    handler = logging.StreamHandler()
    handler.setLevel(options.loglevel)
    formatter = logging.Formatter('[%(levelname)-8s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    slave = Slave(host, port, name=options.name, config=options.config,
                  dry_run=options.dry_run, work_dir=options.work_dir,
                  keep_files=options.keep_files)
    try:
        slave.run()
    except KeyboardInterrupt:
        slave.quit()

    if not options.keep_files and os.path.isdir(slave.work_dir):
        shutil.rmtree(slave.work_dir)

if __name__ == '__main__':
    main()
