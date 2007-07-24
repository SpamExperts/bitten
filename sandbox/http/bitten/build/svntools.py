# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2007 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

"""Recipe commands for Subversion."""

import logging
import posixpath

log = logging.getLogger('bitten.build.svntools')

def checkout(ctxt, url, path=None, revision=None):
    """Perform a checkout from a Subversion repository.
    
    @param ctxt: the build context
    @type ctxt: an instance of L{bitten.recipe.Context}
    @param url: the URL of the repository
    @param path: the path inside the repository
    @param revision: the revision to check out
    """
    args = ['checkout']
    if revision:
        args += ['-r', revision]
    if path:
        url = posixpath.join(url, path)
    args += [url, '.']

    from bitten.build import shtools
    returncode = shtools.execute(ctxt, file_='svn', args=args)
    if returncode != 0:
        ctxt.error('svn checkout failed (%s)' % returncode)

def update(ctxt, revision=None):
    """Update the local working copy from the Subversion repository.
    
    @param ctxt: the build context
    @type ctxt: an instance of L{bitten.recipe.Context}
    @param revision: the revision to check out
    """
    args = ['update']
    if revision:
        args += ['-r', revision]

    from bitten.build import shtools
    returncode = shtools.execute(ctxt, file_='svn', args=args)
    if returncode != 0:
        ctxt.error('svn update failed (%s)' % returncode)
