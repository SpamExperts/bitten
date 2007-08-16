# -*- coding: utf-8 -*-
#
# Copyright (C) 2007 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

import shutil
import tempfile
import unittest

from trac.core import TracError
from trac.db import DatabaseManager
from trac.perm import PermissionCache, PermissionError, PermissionSystem
from trac.test import EnvironmentStub, Mock
from trac.versioncontrol import Repository
from trac.web.clearsilver import HDFWrapper
from trac.web.href import Href
from trac.web.main import Request, RequestDone
from bitten.main import BuildSystem
from bitten.model import BuildConfig, TargetPlatform, Build, schema
from bitten.admin import BuildMasterAdminPageProvider, \
                         BuildConfigurationsAdminPageProvider


class BuildMasterAdminPageProviderTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = tempfile.mkdtemp()

        # Create tables
        db = self.env.get_db_cnx()
        cursor = db.cursor()
        connector, _ = DatabaseManager(self.env)._get_connector()
        for table in schema:
            for stmt in connector.to_sql(table):
                cursor.execute(stmt)

        # Set up permissions
        self.env.config.set('trac', 'permission_store',
                            'DefaultPermissionStore')
        PermissionSystem(self.env).grant_permission('joe', 'BUILD_ADMIN')

        # Hook up a dummy repository
        self.repos = Mock(
            get_node=lambda path, rev=None: Mock(get_history=lambda: [],
                                                 isdir=True),
            normalize_path=lambda path: path,
            sync=lambda: None
        )
        self.env.get_repository = lambda authname=None: self.repos

    def tearDown(self):
        shutil.rmtree(self.env.path)

    def test_get_admin_pages(self):
        provider = BuildMasterAdminPageProvider(self.env)

        req = Mock(perm=PermissionCache(self.env, 'joe'))
        self.assertEqual([('bitten', 'Builds', 'master', 'Master Settings')],
                         list(provider.get_admin_pages(req)))

        PermissionSystem(self.env).revoke_permission('joe', 'BUILD_ADMIN')
        req = Mock(perm=PermissionCache(self.env, 'joe'))
        self.assertEqual([], list(provider.get_admin_pages(req)))

    def test_process_get_request(self):
        data = {}
        req = Mock(method='GET', hdf=data,
                   perm=PermissionCache(self.env, 'joe'))

        provider = BuildMasterAdminPageProvider(self.env)
        template_name, content_type = provider.process_admin_request(
            req, 'bitten', 'master', ''
        )

        self.assertEqual('bitten_admin_master.cs', template_name)
        self.assertEqual(None, content_type)
        assert 'admin.master' in data
        self.assertEqual({
            'slave_timeout': 3600,
            'adjust_timestamps': False,
            'build_all': False,
        }, data['admin.master'])

    def test_process_config_changes(self):
        redirected_to = []
        def redirect(url):
            redirected_to.append(url)
            raise RequestDone
        req = Mock(method='POST', perm=PermissionCache(self.env, 'joe'),
                   abs_href=Href('http://example.org/'), redirect=redirect,
                   args={'slave_timeout': '60', 'adjust_timestamps': ''})

        provider = BuildMasterAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'master', '')
            self.fail('Expected RequestDone')

        except RequestDone:
            self.assertEqual('http://example.org/admin/bitten/master',
                             redirected_to[0])
            section = self.env.config['bitten']
            self.assertEqual(60, section.getint('slave_timeout'))
            self.assertEqual(True, section.getbool('adjust_timestamps'))
            self.assertEqual(False, section.getbool('build_all'))


class BuildConfigurationsAdminPageProviderTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = tempfile.mkdtemp()

        # Create tables
        db = self.env.get_db_cnx()
        cursor = db.cursor()
        connector, _ = DatabaseManager(self.env)._get_connector()
        for table in schema:
            for stmt in connector.to_sql(table):
                cursor.execute(stmt)

        # Set up permissions
        self.env.config.set('trac', 'permission_store',
                            'DefaultPermissionStore')
        PermissionSystem(self.env).grant_permission('joe', 'BUILD_CREATE')
        PermissionSystem(self.env).grant_permission('joe', 'BUILD_DELETE')
        PermissionSystem(self.env).grant_permission('joe', 'BUILD_MODIFY')

        # Hook up a dummy repository
        self.repos = Mock(
            get_node=lambda path, rev=None: Mock(get_history=lambda: [],
                                                 isdir=True),
            normalize_path=lambda path: path,
            sync=lambda: None
        )
        self.env.get_repository = lambda authname=None: self.repos

    def tearDown(self):
        shutil.rmtree(self.env.path)

    def test_get_admin_pages(self):
        provider = BuildConfigurationsAdminPageProvider(self.env)

        req = Mock(perm=PermissionCache(self.env, 'joe'))
        self.assertEqual([('bitten', 'Builds', 'configs', 'Configurations')],
                         list(provider.get_admin_pages(req)))

        PermissionSystem(self.env).revoke_permission('joe', 'BUILD_MODIFY')
        req = Mock(perm=PermissionCache(self.env, 'joe'))
        self.assertEqual([], list(provider.get_admin_pages(req)))

    def test_process_get_request_overview_empty(self):
        data = {}
        req = Mock(method='GET', hdf=data,
                   perm=PermissionCache(self.env, 'joe'))

        provider = BuildConfigurationsAdminPageProvider(self.env)
        template_name, content_type = provider.process_admin_request(
            req, 'bitten', 'configs', ''
        )

        self.assertEqual('bitten_admin_configs.cs', template_name)
        self.assertEqual(None, content_type)
        self.assertEqual([], data['admin']['configs'])

    def test_process_view_configs(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()
        BuildConfig(self.env, name='bar', label='Bar', path='branches/bar',
                    min_rev='123', max_rev='456').insert()

        data = {}
        req = Mock(method='GET', hdf=data, href=Href('/'),
                   perm=PermissionCache(self.env, 'joe'))

        provider = BuildConfigurationsAdminPageProvider(self.env)
        template_name, content_type = provider.process_admin_request(
            req, 'bitten', 'configs', ''
        )

        self.assertEqual('bitten_admin_configs.cs', template_name)
        self.assertEqual(None, content_type)
        configs = data['admin']['configs']
        self.assertEqual(2, len(configs))
        self.assertEqual({
            'name': 'bar', 'href': '/admin/bitten/configs/bar',
            'label': 'Bar', 'min_rev': '123', 'max_rev': '456',
            'path': 'branches/bar', 'active': False
        }, configs[0])
        self.assertEqual({
            'name': 'foo', 'href': '/admin/bitten/configs/foo',
            'label': 'Foo', 'min_rev': None, 'max_rev': None,
            'path': 'branches/foo', 'active': True
        }, configs[1])

    def test_process_view_config(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()
        TargetPlatform(self.env, config='foo', name='any').insert()

        data = {}
        req = Mock(method='GET', hdf=data, href=Href('/'),
                   perm=PermissionCache(self.env, 'joe'))

        provider = BuildConfigurationsAdminPageProvider(self.env)
        template_name, content_type = provider.process_admin_request(
            req, 'bitten', 'configs', 'foo'
        )

        self.assertEqual('bitten_admin_configs.cs', template_name)
        self.assertEqual(None, content_type)
        config = data['admin']['config']
        self.assertEqual({
            'name': 'foo', 'label': 'Foo', 'description': '', 'recipe': '',
            'path': 'branches/foo', 'min_rev': None, 'max_rev': None,
            'active': True, 'platforms': [{
                'href': '/admin/bitten/configs/foo/1',
                'name': 'any',
                'id': 1
            }]
        }, config)

    def test_process_add_config(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()

        redirected_to = []
        def redirect(url):
            redirected_to.append(url)
            raise RequestDone
        req = Mock(method='POST', perm=PermissionCache(self.env, 'joe'),
                   abs_href=Href('http://example.org/'), redirect=redirect,
                   args={'add': '', 'name': 'bar', 'label': 'Bar'})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected RequestDone')

        except RequestDone:
            self.assertEqual('http://example.org/admin/bitten/configs/bar',
                             redirected_to[0])
            config = BuildConfig.fetch(self.env, name='bar')
            self.assertEqual('Bar', config.label)

    def test_process_add_config_no_perms(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()
        PermissionSystem(self.env).revoke_permission('joe', 'BUILD_CREATE')

        req = Mock(method='POST',
                   perm=PermissionCache(self.env, 'joe'),
                   args={'add': '', 'name': 'bar', 'label': 'Bar'})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected PermissionError')

        except PermissionError:
            pass

    def test_process_remove_config(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()
        BuildConfig(self.env, name='bar', label='Bar', path='branches/bar',
                    min_rev='123', max_rev='456').insert()

        redirected_to = []
        def redirect(url):
            redirected_to.append(url)
            raise RequestDone
        req = Mock(method='POST', perm=PermissionCache(self.env, 'joe'),
                   abs_href=Href('http://example.org/'), redirect=redirect,
                   args={'remove': '', 'sel': 'bar'})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected RequestDone')

        except RequestDone:
            self.assertEqual('http://example.org/admin/bitten/configs',
                             redirected_to[0])
            assert not BuildConfig.fetch(self.env, name='bar')

    def test_process_remove_config_no_selection(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()

        req = Mock(method='POST', perm=PermissionCache(self.env, 'joe'),
                   args={'remove': ''})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected TracError')

        except TracError, e:
            self.assertEqual('No configuration selected', e.message)

    def test_process_remove_config_bad_selection(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()

        req = Mock(method='POST', perm=PermissionCache(self.env, 'joe'),
                   args={'remove': '', 'sel': 'baz'})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected TracError')

        except TracError, e:
            self.assertEqual("Configuration 'baz' not found", e.message)

    def test_process_remove_config_no_perms(self):
        BuildConfig(self.env, name='foo', label='Foo', path='branches/foo',
                    active=True).insert()
        PermissionSystem(self.env).revoke_permission('joe', 'BUILD_DELETE')

        req = Mock(method='POST',
                   perm=PermissionCache(self.env, 'joe'),
                   args={'remove': '', 'sel': 'bar'})

        provider = BuildConfigurationsAdminPageProvider(self.env)
        try:
            provider.process_admin_request(req, 'bitten', 'configs', '')
            self.fail('Expected PermissionError')

        except PermissionError:
            pass


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(
        BuildMasterAdminPageProviderTestCase, 'test'
    ))
    suite.addTest(unittest.makeSuite(
        BuildConfigurationsAdminPageProviderTestCase, 'test'
    ))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
