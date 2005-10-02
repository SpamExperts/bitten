# -*- coding: iso8859-1 -*-
#
# Copyright (C) 2005 Christopher Lenz <cmlenz@gmx.de>
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://bitten.cmlenz.net/wiki/License.

"""Utility code for easy input and output of XML.

The current implementation uses `xml.dom.minidom` under the hood for parsing.
"""

import os
try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

__all__ = ['Element', 'parse']

def _escape_text(text):
    """Escape special characters in the provided text so that it can be safely
    included in XML text nodes.
    """
    return str(text).replace('&', '&amp;').replace('<', '&lt;') \
                    .replace('>', '&gt;')

def _escape_attr(attr):
    """Escape special characters in the provided text so that it can be safely
    included in XML attribute values.
    """
    return _escape_text(attr).replace('"', '&#34;')


class Fragment(object):
    """A collection of XML elements."""
    __slots__ = ['children']

    def __init__(self):
        """Create an XML fragment."""
        self.children = []

    def __getitem__(self, nodes):
        """Add nodes to the fragment."""
        if not isinstance(nodes, (list, tuple)):
            nodes = [nodes]
        for node in nodes:
            self.append(node)
        return self

    def __str__(self):
        """Return a string representation of the XML fragment."""
        buf = StringIO()
        self.write(buf)
        return buf.getvalue()

    def append(self, node):
        """Append an element or fragment as child."""
        if isinstance(node, Element):
            self.children.append(node)
        elif isinstance(node, Fragment):
            self.children += node.children
        elif node is not None and node != '':
            self.children.append(str(node))

    def write(self, out, newlines=False):
        """Serializes the element and writes the XML to the given output
        stream.
        """
        for child in self.children:
            if isinstance(child, (Element, ParsedElement)):
                child.write(out, newlines=newlines)
            else:
                if child[0] == '<':
                    out.write('<![CDATA[' + child + ']]>')
                else:
                    out.write(_escape_text(child))


class Element(Fragment):
    """Simple XML output generator based on the builder pattern.

    Construct XML elements by passing the tag name to the constructor:

    >>> print Element('foo')
    <foo/>

    Attributes can be specified using keyword arguments. The values of the
    arguments will be converted to strings and any special XML characters
    escaped:

    >>> print Element('foo', bar=42)
    <foo bar="42"/>
    >>> print Element('foo', bar='1 < 2')
    <foo bar="1 &lt; 2"/>
    >>> print Element('foo', bar='"baz"')
    <foo bar="&#34;baz&#34;"/>

    The order in which attributes are rendered is undefined.

    Elements can be using item access notation:

    >>> print Element('foo')[Element('bar'), Element('baz')]
    <foo><bar/><baz/></foo>

    Text nodes can be nested in an element by using strings instead of elements
    in item access. Any special characters in the strings are escaped
    automatically:

    >>> print Element('foo')['Hello world']
    <foo>Hello world</foo>
    >>> print Element('foo')[42]
    <foo>42</foo>
    >>> print Element('foo')['1 < 2']
    <foo>1 &lt; 2</foo>

    This technique also allows mixed content:

    >>> print Element('foo')['Hello ', Element('b')['world']]
    <foo>Hello <b>world</b></foo>

    Finally, text starting with an opening angle bracket is treated specially:
    under the assumption that the text actually contains XML itself, the whole
    thing is wrapped in a CDATA block instead of escaping all special characters
    individually:

    >>> print Element('foo')['<bar a="3" b="4"><baz/></bar>']
    <foo><![CDATA[<bar a="3" b="4"><baz/></bar>]]></foo>
    """
    __slots__ = ['name', 'attr']

    def __init__(self, name_, **attr):
        """Create an XML element using the specified tag name.
        
        The tag name must be supplied as the first positional argument. All
        keyword arguments following it are handled as attributes of the element.
        """
        Fragment.__init__(self)
        self.name = name_
        self.attr = dict([(name, value) for name, value in attr.items()
                          if value is not None])

    def write(self, out, newlines=False):
        """Serializes the element and writes the XML to the given output
        stream.
        """
        out.write('<')
        out.write(self.name)
        for name, value in self.attr.items():
            out.write(' %s="%s"' % (name, _escape_attr(value)))
        if self.children:
            out.write('>')
            Fragment.write(self, out, newlines)
            out.write('</' + self.name + '>')
        else:
            out.write('/>')
        if newlines:
            out.write(os.linesep)


class SubElement(Element):
    """Element that is appended as a new child to another element on
    construction.
    """
    __slots__ = []

    def __init__(self, parent_, name_, **attr):
        """Create an XML element using the specified tag name.
        
        The first argument is the instance of the parent element that this
        subelement should be appended to; the second argument is the name of the
        tag. All keyword arguments are added as attributes of the element.
        """
        Element.__init__(self, name_, **attr)
        parent_.append(self)


class ParseError(Exception):
    """Exception thrown when there's an error parsing an XML document."""


def parse(text_or_file):
    """Parse an XML document provided as string or file-like object.
    
    Returns an instance of `ParsedElement` that can be used to traverse the
    parsed document.
    """
    from xml.dom import minidom
    from xml.parsers import expat
    try:
        if isinstance(text_or_file, (str, unicode)):
            dom = minidom.parseString(text_or_file)
        else:
            dom = minidom.parse(text_or_file)
        return ParsedElement(dom.documentElement)
    except expat.error, e:
        raise ParseError, e


class ParsedElement(object):
    """Representation of an XML element that was parsed from a string or
    file.
    """
    __slots__ = ['_node', 'attr']

    def __init__(self, node):
        self._node = node
        self.attr = dict([(name.encode(), value.encode()) for name, value
                          in node.attributes.items()])

    name = property(fget=lambda self: self._node.localName)
    namespace = property(fget=lambda self: self._node.namespaceURI)

    def children(self, name=None):
        """Iterate over the child elements of this element.

        If the parameter `name` is provided, only include elements with a
        matching local name. Otherwise, include all elements.
        """
        for child in [c for c in self._node.childNodes if c.nodeType == 1]:
            if name in (None, child.tagName):
                yield ParsedElement(child)

    def __iter__(self):
        return self.children()

    def gettext(self):
        """Return the text content of this element.
        
        This concatenates the values of all text nodes that are immediate
        children of this element.
        """
        return ''.join([c.nodeValue or '' for c in self._node.childNodes])

    def write(self, out, newlines=False):
        """Serializes the element and writes the XML to the given output
        stream.
        """
        self._node.writexml(out, newl=newlines and '\n' or '')

    def __str__(self):
        """Return a string representation of the XML element."""
        buf = StringIO()
        self.write(buf)
        return buf.getvalue()


if __name__ == '__main__':
    import doctest
    doctest.testmod()