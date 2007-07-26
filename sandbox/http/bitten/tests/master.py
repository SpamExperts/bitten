# -*- coding: utf-8 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

from datetime import datetime
import re
import shutil
from StringIO import StringIO
import tempfile
import unittest

from trac.perm import PermissionCache, PermissionSystem
from trac.test import EnvironmentStub, Mock
from trac.web.api import HTTPMethodNotAllowed, HTTPNotFound, RequestDone
from trac.web.href import Href

from bitten.master import BuildMaster
from bitten.model import BuildConfig, TargetPlatform, Build, schema
from bitten.trac_ext.compat import schema_to_sql
from bitten.trac_ext.main import BuildSystem


class BuildMasterTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = tempfile.mkdtemp()

        PermissionSystem(self.env).grant_permission('hal', 'BUILD_EXEC')

        # Create tables
        db = self.env.get_db_cnx()
        cursor = db.cursor()
        for table in schema:
            for stmt in schema_to_sql(self.env, db, table):
                cursor.execute(stmt)

        self.repos = Mock()
        self.env.get_repository = lambda authname=None: self.repos

    def tearDown(self):
        shutil.rmtree(self.env.path)

    def test_create_build(self):
        BuildConfig(self.env, 'test', path='somepath', active=True).insert()
        platform = TargetPlatform(self.env, config='test', name="Unix")
        platform.rules.append(('family', 'posix'))
        platform.insert()

        self.repos = Mock(
            get_node=lambda path, rev=None: Mock(
                get_entries=lambda: [Mock(), Mock()],
                get_history=lambda: [('somepath', 123, 'edit'),
                                     ('somepath', 121, 'edit'),
                                     ('somepath', 120, 'edit')]
            ),
            get_changeset=lambda rev: Mock(date=42),
            normalize_path=lambda path: path,
            rev_older_than=lambda rev1, rev2: rev1 < rev2
        )

        inheaders = {'Content-Type': 'application/x-bitten+xml'}
        inbody = StringIO("""<slave name="hal">
  <platform>Power Macintosh</platform>
  <os family="posix" version="8.1.0">Darwin</os>
</slave>""")
        outheaders = {}
        outbody = StringIO()
        req = Mock(method='POST', base_path='', path_info='/builds',
                   href=Href('/trac'), abs_href=Href('http://example.org/trac'),
                   remote_addr='127.0.0.1', args={},
                   perm=PermissionCache(self.env, 'hal'),
                   get_header=lambda x: inheaders.get(x), read=inbody.read,
                   send_response=lambda x: outheaders.setdefault('Status', x),
                   send_header=lambda x, y: outheaders.setdefault(x, y),
                   write=outbody.write)

        module = BuildMaster(self.env)
        assert module.match_request(req)
        try:
            module.process_request(req)
            self.fail('Expected RequestDone')
        except RequestDone:
            self.assertEqual(201, outheaders['Status'])
            self.assertEqual('text/plain', outheaders['Content-Type'])
            location = outheaders['Location']
            mo = re.match('http://example.org/trac/builds/(\d+)', location)
            assert mo, 'Location was %r' % location
            self.assertEqual('Build pending', outbody.getvalue())
            build = Build.fetch(self.env, int(mo.group(1)))
            self.assertEqual(Build.IN_PROGRESS, build.status)
            self.assertEqual('hal', build.slave)

    def test_create_build_no_post(self):
        req = Mock(method='GET', base_path='', path_info='/builds',
                   href=Href('/trac'), remote_addr='127.0.0.1', args={},
                   perm=PermissionCache(self.env, 'hal'))
        module = BuildMaster(self.env)
        assert module.match_request(req)
        try:
            module.process_request(req)
            self.fail('Expected HTTPMethodNotAllowed')
        except HTTPMethodNotAllowed:
            pass

    def test_create_build_no_match(self):
        inheaders = {'Content-Type': 'application/x-bitten+xml'}
        inbody = StringIO("""<slave name="hal">
  <platform>Power Macintosh</platform>
  <os family="posix" version="8.1.0">Darwin</os>
</slave>""")
        outheaders = {}
        outbody = StringIO()
        req = Mock(method='POST', base_path='', path_info='/builds',
                   href=Href('/trac'), remote_addr='127.0.0.1', args={},
                   perm=PermissionCache(self.env, 'hal'),
                   get_header=lambda x: inheaders.get(x), read=inbody.read,
                   send_response=lambda x: outheaders.setdefault('Status', x),
                   send_header=lambda x, y: outheaders.setdefault(x, y),
                   write=outbody.write)

        module = BuildMaster(self.env)
        assert module.match_request(req)
        try:
            module.process_request(req)
            self.fail('Expected RequestDone')
        except RequestDone:
            self.assertEqual(204, outheaders['Status'])
            self.assertEqual('text/plain', outheaders['Content-Type'])
            self.assertEqual('No pending builds', outbody.getvalue())

    def test_no_such_build(self):
        req = Mock(method='GET', base_path='',
                   path_info='/builds/123', href=Href('/trac'),
                   remote_addr='127.0.0.1', args={},
                   perm=PermissionCache(self.env, 'hal'))

        module = BuildMaster(self.env)
        assert module.match_request(req)
        try:
            module.process_request(req)
            self.fail('Expected HTTPNotFound')
        except HTTPNotFound:
            pass

    def test_fetch_recipe(self):
        config = BuildConfig(self.env, 'test', path='somepath', active=True,
                             recipe='<build></build>')
        config.insert()
        platform = TargetPlatform(self.env, config='test', name="Unix")
        platform.rules.append(('family', 'posix'))
        platform.insert()
        build = Build(self.env, 'test', '123', platform.id, slave='hal',
                      rev_time=42)
        build.insert()

        outheaders = {}
        outbody = StringIO()
        
        req = Mock(method='GET', base_path='',
                   path_info='/builds/%d' % build.id,
                   href=Href('/trac'), remote_addr='127.0.0.1', args={},
                   perm=PermissionCache(self.env, 'hal'),
                   send_response=lambda x: outheaders.setdefault('Status', x),
                   send_header=lambda x, y: outheaders.setdefault(x, y),
                   write=outbody.write)

        module = BuildMaster(self.env)
        assert module.match_request(req)
        try:
            module.process_request(req)
            self.fail('Expected RequestDone')
        except RequestDone:
            self.assertEqual(200, outheaders['Status'])
            self.assertEqual('application/x-bitten+xml',
                             outheaders['Content-Type'])
            self.assertEqual('<build path="somepath" revision="123"/>',
                             outbody.getvalue())


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(BuildMasterTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
