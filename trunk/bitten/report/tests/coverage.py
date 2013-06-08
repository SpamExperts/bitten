# -*- coding: utf-8 -*-
#
# Copyright (C) 2005-2007 Christopher Lenz <cmlenz@gmx.de>
# Copyright (C) 2007-2010 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

import unittest

from genshi.builder import tag
from trac.db import DatabaseManager
from trac.mimeview import Context
from trac.test import EnvironmentStub, Mock, MockPerm
from trac.util.datefmt import to_datetime, utc
from trac.web.href import Href
from bitten.model import *
from bitten.report import coverage
from bitten.report.coverage import TestCoverageChartGenerator, \
                                   TestCoverageAnnotator

def env_stub_with_tables():
    env = EnvironmentStub(enable=['trac.*', 'bitten.*'])
    db = env.get_db_cnx()
    cursor = db.cursor()
    connector, _ = DatabaseManager(env)._get_connector()
    for table in schema:
        for stmt in connector.to_sql(table):
            cursor.execute(stmt)
    return env

class TestCoverageChartGeneratorTestCase(unittest.TestCase):

    def setUp(self):
        self.env = env_stub_with_tables()
        self.env.path = ''

    def test_supported_categories(self):
        generator = TestCoverageChartGenerator(self.env)
        self.assertEqual(['coverage'], generator.get_supported_categories())

    def test_no_reports(self):
        req = Mock()
        config = Mock(name='trunk', min_rev_time=lambda env: 0, 
                      max_rev_time=lambda env: 1000)
        generator = TestCoverageChartGenerator(self.env)
        template, data = generator.generate_chart_data(req, config, 'coverage')
        self.assertEqual('json.txt', template)
        data = data['json']
        self.assertEqual('Test Coverage', data['title'])
        actual_data = data['data']
        self.assertEqual('Lines of code', actual_data[0]['label'])
        self.assertEqual('Coverage', actual_data[1]['label'])

    def test_single_platform(self):
        config = Mock(name='trunk', min_rev_time=lambda env: 0, 
                      max_rev_time=lambda env: 1000)
        build = Build(self.env, config='trunk', platform=1, rev=123,
                      rev_time=42)
        build.insert()
        report = Report(self.env, build=build.id, step='foo',
                        category='coverage')
        report.items += [{'lines': '12', 'percentage': '25'}]
        report.insert()

        req = Mock()
        generator = TestCoverageChartGenerator(self.env)
        template, data = generator.generate_chart_data(req, config, 'coverage')
        self.assertEqual('json.txt', template)
        data = data['json']
        self.assertEqual('Test Coverage', data['title'])
        actual_data = data['data']
        self.assertEqual('123', actual_data[0]['data'][0][0])
        self.assertEqual('Lines of code', actual_data[0]['label'])
        self.assertEqual(12, actual_data[0]['data'][0][1])
        self.assertEqual('Coverage', actual_data[1]['label'])
        self.assertEqual(3, actual_data[1]['data'][0][1])

    def test_multi_platform(self):
        config = Mock(name='trunk', min_rev_time=lambda env: 0, 
                      max_rev_time=lambda env: 1000)
        build = Build(self.env, config='trunk', platform=1, rev=123,
                      rev_time=42)
        build.insert()
        report = Report(self.env, build=build.id, step='foo',
                        category='coverage')
        report.items += [{'lines': '12', 'percentage': '25'}]
        report.insert()
        build = Build(self.env, config='trunk', platform=2, rev=123,
                      rev_time=42)
        build.insert()
        report = Report(self.env, build=build.id, step='foo',
                        category='coverage')
        report.items += [{'lines': '12', 'percentage': '50'}]
        report.insert()

        req = Mock()
        generator = TestCoverageChartGenerator(self.env)
        template, data = generator.generate_chart_data(req, config, 'coverage')
        self.assertEqual('json.txt', template)
        data = data['json']
        self.assertEqual('Test Coverage', data['title'])
        actual_data = data['data']
        self.assertEqual('123', actual_data[0]['data'][0][0])
        self.assertEqual('Lines of code', actual_data[0]['label'])
        self.assertEqual(12, actual_data[0]['data'][0][1])
        self.assertEqual('Coverage', actual_data[1]['label'])
        self.assertEqual(6, actual_data[1]['data'][0][1])


class TestCoverageAnnotatorTestCase(unittest.TestCase):

    def setUp(self):
        self.env = env_stub_with_tables()
        self.env.path = ''
        # Hook up a dummy repository
        self.repos = Mock()
        self.env.get_repository = lambda authname=None: self.repos # 0.11
        try: # 0.12+
            from trac.core import Component, implements
            from trac.versioncontrol.api import IRepositoryConnector, \
                                                IRepositoryProvider
            class DummyRepos(Component):
                implements(IRepositoryConnector, IRepositoryProvider)
                def get_supported_types(self):
                    yield ('dummy', 9)
                def get_repository(this, repos_type, repos_dir, params):
                    return self.repos # Note: 'this' vs 'self' usage
                def get_repositories(self):
                    yield ('', {'dir': 'dummy_dir', 'type': 'dummy'})
            self.dummy = DummyRepos
        except ImportError:
            self.dummy = None # not supported, will use get_repository()

    def tearDown(self):
        if self.dummy: # remove from components list + interfaces dict
            self.env.__metaclass__._components.remove(self.dummy)
            for key in self.env.__metaclass__._registry.keys():
                if self.dummy in self.env.__metaclass__._registry[key]:
                    self.env.__metaclass__._registry[key].remove(self.dummy)

    def test_converted_doctest(self):
        self.repos.get_changeset=lambda x: Mock(date=to_datetime(12345, utc))

        BuildConfig(self.env, name='trunk', path='trunk').insert()
        Build(self.env, rev=123, config='trunk', rev_time=12345, platform=1
                                                    ).insert()
        rpt = Report(self.env, build=1, step='test', category='coverage')
        rpt.items.append({'file': 'foo.py', 'line_hits': '5 - 0'})
        rpt.insert()

        ann = TestCoverageAnnotator(self.env)
        req = Mock(href=Href('/'), perm=MockPerm(),
                    chrome={'warnings': []}, args={})

        # Version in the branch should not match:
        context = Context.from_request(req, 'source', '/branches/blah/foo.py', 123)
        self.assertEquals(ann.get_annotation_data(context), [])

        # Version in the trunk should match:
        context = Context.from_request(req, 'source', '/trunk/foo.py', 123)
        data = ann.get_annotation_data(context)
        self.assertEquals(data, [u'5', u'-', u'0'])

        def annotate_row(lineno, line):
            row = tag.tr()
            ann.annotate_row(context, row, lineno, line, data)
            return unicode(row.generate().render('html'))

        self.assertEquals(annotate_row(1, 'x = 1'),
                            u'<tr><th class="covered">5</th></tr>')
        self.assertEquals(annotate_row(2, ''),
                            u'<tr><th></th></tr>')
        self.assertEquals(annotate_row(3, 'y = x'),
                            u'<tr><th class="uncovered">0</th></tr>')


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestCoverageChartGeneratorTestCase))
    suite.addTest(unittest.makeSuite(TestCoverageAnnotatorTestCase))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
