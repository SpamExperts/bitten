# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

import md5
import os
import tarfile
import time
import zipfile


class Error(Exception):
    """Error raised when packing or unpacking a snapshot archive fails."""


_formats = {'gzip': ('.tar.gz', 'gz'), 'bzip2': ('.tar.bz2', 'bz2'),
            'zip': ('.zip', None)}

def index(env, prefix):
    """Generator that yields `(rev, format, path)` tuples for every archive in
    the environment snapshots directory that match the specified prefix.
    """
    filedir = os.path.join(env.path, 'snapshots')
    for filename in [f for f in os.listdir(filedir) if f.startswith(prefix)]:
        rest = filename[len(prefix):]

        # Determine format based of file extension
        format = None
        for name, (extension, _) in _formats.items():
            if rest.endswith(extension):
                rest = rest[:-len(extension)]
                format = name
        if not format:
            continue

        if not rest.startswith('_r'):
            continue
        rev = rest[2:]

        expected_md5sum = _make_md5sum(os.path.join(filedir, filename))
        md5sum_path = os.path.join(filedir, filename + '.md5')
        if not os.path.isfile(md5sum_path):
            continue
        md5sum_file = file(md5sum_path)
        try:
            existing_md5sum = md5sum_file.read()
            if existing_md5sum != expected_md5sum:
                continue
        finally:
            md5sum_file.close()

        yield rev, format, os.path.join(filedir, filename)

def _make_md5sum(filename):
    """Generate an MD5 checksum for the specified file."""
    md5sum = md5.new()
    fileobj = file(filename, 'rb')
    try:
        while True:
            chunk = fileobj.read(4096)
            if not chunk:
                break
            md5sum.update(chunk)
    finally:
        fileobj.close()
    return md5sum.hexdigest() + '  ' + filename

def pack(env, repos=None, path=None, rev=None, prefix=None, format='gzip',
         overwrite=False):
    """Create a snapshot archive in the specified format."""
    if format not in _formats:
        raise Error, 'Unknown archive format: %s' % format

    if repos is None:
        repos = env.get_repository()
    root = repos.get_node(path or '/', rev)
    if not root.isdir:
        raise Error, '"%s" is not a directory' % path

    filedir = os.path.join(env.path, 'snapshots')
    if not os.access(filedir, os.R_OK + os.W_OK):
        raise Error, 'Insufficient permissions to create tarball'
    if not prefix:
        prefix = root.path.replace('/', '-')
    prefix += '_r%s' % root.rev
    filename = os.path.join(filedir, prefix + _formats[format][0])

    if not overwrite and os.path.isfile(filename):
        return filename

    if format in ('bzip2', 'gzip'):
        archive = tarfile.open(filename, 'w:' + _formats[format][1])
    else:
        archive = zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED)

    def _add_entry(node):
        name = node.path[len(root.path):]
        if name.startswith('/'):
            name = name[1:]
        if node.isdir:
            if format == 'zip':
                dirpath = os.path.join(prefix, name).rstrip('/\\') + '/'
                info = zipfile.ZipInfo(dirpath)
                archive.writestr(info, '')
            for entry in node.get_entries():
                _add_entry(entry)
        elif format in ('bzip2', 'gzip'):
            try:
                info = tarfile.TarInfo(os.path.join(prefix, name))
                info.type = tarfile.REGTYPE
                info.mtime = node.last_modified
                info.size = node.content_length
                archive.addfile(info, node.get_content())
            except tarfile.TarError, e:
                raise Error, e
        else: # ZIP format
            try:
                info = zipfile.ZipInfo(os.path.join(prefix, name))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.date_time = time.gmtime(node.last_modified)[:6]
                info.file_size = node.content_length
                archive.writestr(info, node.get_content().read())
            except zipfile.error, e:
                raise Error, e
    try:
        _add_entry(root)
    finally:
        archive.close()

    # Create MD5 checksum
    md5sum = _make_md5sum(filename)
    md5sum_file = file(filename + '.md5', 'w')
    try:
        md5sum_file.write(md5sum)
    finally:
        md5sum_file.close()

    return filename

def unpack(filename, dest_path, format=None):
    """Extract the contents of a snapshot archive."""
    if not format:
        for name, (extension, _) in _formats.items():
            if filename.endswith(extension):
                format = name
                break
        if not format:
            raise Error, 'Unkown archive extension: %s' \
                         % os.path.splitext(filename)[1]

    names = []
    if format in ('bzip2', 'gzip'):
        try:
            tar_file = tarfile.open(filename)
            try:
                tar_file.chown = lambda *args: None # Don't chown extracted members
                for tarinfo in tar_file:
                    if tarinfo.isfile() or tarinfo.isdir():
                        if tarinfo.name.startswith('/') or '..' in tarinfo.name:
                            continue
                        names.append(tarinfo.name)
                        tar_file.extract(tarinfo, dest_path)
            finally:
                tar_file.close()
        except tarfile.TarError, e:
            raise Error, e
    elif format == 'zip':
        try:
            zip_file = zipfile.ZipFile(filename, 'r')
            try:
                for name in zip_file.namelist():
                    names.append(name)
                    path = os.path.join(dest_path, name)
                    if name.endswith('/'):
                        os.makedirs(path)
                    else:
                        dirname = os.path.dirname(path)
                        if not os.path.isdir(dirname):
                            os.makedirs(dirname)
                        dest_file = file(path, 'wb')
                        try:
                            dest_file.write(zip_file.read(name))
                        finally:
                            dest_file.close()
            finally:
                zip_file.close()
        except (IOError, zipfile.error), e:
            raise Error, e
    return os.path.commonprefix(names)