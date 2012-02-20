# -*- coding: utf-8 -*-
#
# Maintained by W. Martin Borgert <debacle@debian.org>
#
# Copyright (C) 2012 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.edgewall.org/wiki/License.

"""Utility for checking that Bitten recipes conform to a RelaxNG XML schema."""

from __future__ import with_statement

import lxml.etree

__version__ = "1.0a1"


def checkrecipe(bitten_recipe_file, relaxng_file):
    """Check a file containing a Bitten recipe."""
    relaxng = lxml.etree.RelaxNG(lxml.etree.parse(relaxng_file))
    relaxng.assertValid(lxml.etree.parse(bitten_recipe_file))


def main():
    from optparse import OptionParser

    parser = OptionParser(usage='usage: %prog recipe_filename',
                          version='%%prog %s' % __version__)
    parser.add_option("-r", "--relaxng", dest="relaxng",
                      default="bitten-recipe.rng",
                      help="RelaxNG schema recipe should conform too")

    options, args = parser.parse_args()

    if len(args) != 1:
        parser.error('incorrect number of arguments')

    [recipe_filename] = args
    relaxng_filename = options.relaxng

    with open(relaxng_filename, "rb") as relaxng_file:
        with open(recipe_filename, "rb") as recipe_file:
            checkrecipe(recipe_file, relaxng_file)
            print "Recipe OK."


if __name__ == "__main__":
    import sys
    sys.exit(main())
