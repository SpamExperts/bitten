# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import os
import shutil
import tempfile
import unittest

from trac.test import EnvironmentStub, Mock
from bitten.util import archive


class IndexTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = tempfile.mkdtemp(prefix='bitten_test')
        os.mkdir(os.path.join(self.env.path, 'snapshots'))

    def tearDown(self):
        shutil.rmtree(self.env.path)

    def _create_file(self, *path):
        filename = os.path.join(self.env.path, *path)
        fd = file(filename, 'w')
        fd.close()
        return filename

    def test_index_formats(self):
        targz_path = self._create_file('snapshots', 'foo_r123.tar.gz')
        tarbz2_path = self._create_file('snapshots', 'foo_r123.tar.bz2')
        zip_path = self._create_file('snapshots', 'foo_r123.zip')
        index = list(archive.index(self.env, 'foo'))
        self.assertEqual(3, len(index))
        assert ('123', 'gzip', targz_path) in index
        assert ('123', 'bzip2', tarbz2_path) in index
        assert ('123', 'zip', zip_path) in index

    def test_index_revs(self):
        rev123_path = self._create_file('snapshots', 'foo_r123.tar.gz')
        rev124_path = self._create_file('snapshots', 'foo_r124.tar.gz')
        index = list(archive.index(self.env, 'foo'))
        self.assertEqual(2, len(index))
        assert ('123', 'gzip', rev123_path) in index
        assert ('124', 'gzip', rev124_path) in index

    def test_index_empty(self):
        index = list(archive.index(self.env, 'foo'))
        self.assertEqual(0, len(index))

    def test_index_prefix(self):
        path = self._create_file('snapshots', 'foo_r123.tar.gz')
        self._create_file('snapshots', 'bar_r123.tar.gz')
        index = list(archive.index(self.env, 'foo'))
        self.assertEqual(1, len(index))
        assert ('123', 'gzip', path) in index

    def test_index_no_rev(self):
        path = self._create_file('snapshots', 'foo_r123.tar.gz')
        self._create_file('snapshots', 'foo_map.tar.gz')
        index = list(archive.index(self.env, 'foo'))
        self.assertEqual(1, len(index))
        assert ('123', 'gzip', path) in index


class PackTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub()
        self.env.path = tempfile.mkdtemp(prefix='bitten_test')
        os.mkdir(os.path.join(self.env.path, 'snapshots'))

    def tearDown(self):
        shutil.rmtree(self.env.path)

    def _create_file(self, *path):
        filename = os.path.join(self.env.path, *path)
        fd = file(filename, 'w')
        fd.close()
        return filename

    def test_pack_unknown_format(self):
        self.assertRaises(archive.Error, archive.pack, self.env, format='foo')

    def test_pack_not_a_directory(self):
        repos = Mock(get_node=lambda path, rev: Mock(isdir=False))
        self.assertRaises(archive.Error, archive.pack, self.env, repos)

    def test_pack_insufficient_perms(self):
        os.chmod(os.path.join(self.env.path, 'snapshots'), 0500)
        repos = Mock(get_node=lambda path, rev: Mock(isdir=True))
        self.assertRaises(archive.Error, archive.pack, self.env, repos)


class UnpackTestCase(unittest.TestCase):

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='bitten_test')

    def tearDown(self):
        shutil.rmtree(self.workdir)

    def _create_file(self, *path):
        filename = os.path.join(self.workdir, *path)
        fd = file(filename, 'w')
        fd.close()
        return filename

    def test_unpack_unknown_format(self):
        self.assertRaises(archive.Error, archive.unpack, 'test.foo',
                          self.workdir)

    def test_unpack_invalid_tar_gz(self):
        path = self._create_file('invalid.tar.gz')
        targz = file(path, 'w')
        targz.write('INVALID')
        targz.close()
        self.assertRaises(archive.Error, archive.unpack, path, self.workdir)

    def test_unpack_invalid_tar_bz2(self):
        path = self._create_file('invalid.tar.bz2')
        targz = file(path, 'w')
        targz.write('INVALID')
        targz.close()
        self.assertRaises(archive.Error, archive.unpack, path, self.workdir)

    def test_unpack_invalid_zip_1(self):
        """
        Verify handling of `IOError` exceptions when trying to unpack an
        invalid ZIP file.

        The `zipfile` module will actually raise an `IOError` instead of a
        `zipfile.error` here because it'll try to seek past the beginning of
        the file.
        """
        path = self._create_file('invalid.zip')
        targz = file(path, 'w')
        targz.write('INVALID')
        targz.close()
        self.assertRaises(archive.Error, archive.unpack, path, self.workdir)

    def test_unpack_invalid_zip_2(self):
        """
        Verify handling of `zip.error` exceptions when trying to unpack an
        invalid ZIP file.
        """
        path = self._create_file('invalid.zip')
        targz = file(path, 'w')
        targz.write('INVALIDINVALIDINVALIDINVALIDINVALIDINVALID')
        targz.close()
        self.assertRaises(archive.Error, archive.unpack, path, self.workdir)


def suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(IndexTestCase, 'test'))
    suite.addTest(unittest.makeSuite(PackTestCase, 'test'))
    suite.addTest(unittest.makeSuite(UnpackTestCase, 'test'))
    return suite

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
