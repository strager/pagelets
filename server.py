#!/usr/bin/env python3.4

import abc
import html
import http.server
import io
import json
import logging
import time

logger = logging.getLogger(__name__)

class HTMLPagelet(object, metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def is_content_loaded(self):
        raise NotImplementedError()

    @abc.abstractmethod
    def write_in_place(self, buffer, placeholder_index_factory):
        raise NotImplementedError()

    def placeholder_id(self, placeholder_index):
        return '__HTMLPagelet_placeholder_{}'.format(placeholder_index)

    def encoding(self):
        return 'utf-8'

    def write_placeholder(self, buffer, placeholder_index):
        placeholder_id = self.placeholder_id(placeholder_index)
        # TODO(strager): Make inline (<span>) versus block (<div>) configurable.
        buffer.write('<div style="display:none;" id={placeholder_id}></div>'.format(
            placeholder_id=html.escape(placeholder_id, quote=True),
        ).encode(self.encoding()))
        return [self]

    def write_fixup(self, buffer, placeholder_index, placeholder_index_factory):
        encoding = self.encoding()
        with io.BytesIO() as temp_buffer:
            pending_pagelets = self.write_in_place(temp_buffer, placeholder_index_factory=placeholder_index_factory)
            js = '''(function () {{
function fixScriptElements(root) {{
    var scripts = root.querySelectorAll('script');
    scripts = Array.prototype.slice.call(scripts);
    var i;
    for (i = 0; i < scripts.length; ++i) {{
        var oldScript = scripts[i];
        var newScript = document.createElement('script');
        if (oldScript.src !== '') {{
            newScript.src = oldScript.src;
        }}
        newScript.text = oldScript.text;
        if (oldScript.type !== '') {{
            newScript.type = oldScript.type;
        }}
        oldScript.parentNode.replaceChild(newScript, oldScript);
    }}
}}

var placeholder = document.getElementById({placeholder_id});
var html = {html};

var placeholderParent = placeholder.parentNode;
var replacement;
var container;
switch (placeholderParent.nodeType) {{
    case Node.ELEMENT_NODE:
        container = document.createElement(placeholderParent.nodeName);
        container.innerHTML = html;
        fixScriptElements(container);
        replacement = document.createDocumentFragment();
        while (container.firstChild) {{
            replacement.appendChild(container.removeChild(container.firstChild));
        }}
        break;
    default:
        throw Exception('Not implemented')
}}
placeholderParent.replaceChild(replacement, placeholder);

// Remove this <script> element from the DOM.
var scripts = document.getElementsByTagName('script');
var script = scripts[scripts.length - 1];
script.parentNode.removeChild(script);
}}());'''.format(
                # HACK(strager): We should properly HTML-escape.
                html=json.dumps(temp_buffer.getvalue().decode(encoding)).replace('<', r'\u003c'),
                placeholder_id=json.dumps(self.placeholder_id(placeholder_index)),
            )
        # FIXME(strager): We should properly HTML-escape.
        buffer.write('<script>{js}</script>'.format(js=js).encode(encoding))
        return pending_pagelets

class LiteralHTMLPagelet(HTMLPagelet):
    def __init__(self, html):
        super(LiteralHTMLPagelet, self).__init__()
        self.__html = html

    def write_in_place(self, buffer, placeholder_index_factory):
        buffer.write(str.encode(self.__html, self.encoding()))
        return []

    def is_content_loaded(self):
        return True

class TriggeredHTMLPagelet(HTMLPagelet):
    def __init__(self, pagelet):
        super(TriggeredHTMLPagelet, self).__init__()
        self.__pagelet = pagelet
        self.__loaded = False

    def set_loaded(self):
        self.__loaded = True

    def write_in_place(self, buffer, placeholder_index_factory):
        if self.__loaded:
            return self.__pagelet.write_in_place(
                buffer,
                placeholder_index_factory=placeholder_index_factory,
            )
        else:
            self.write_placeholder(
                buffer,
                placeholder_index=placeholder_index_factory(self),
            )
            return [self]

    def is_content_loaded(self):
        return self.__loaded

class MultiHTMLPagelet(HTMLPagelet):
    def __init__(self, pagelets):
        super(MultiHTMLPagelet, self).__init__()
        self.__pagelets = list(pagelets)

    def write_in_place(self, buffer, placeholder_index_factory):
        pending_pagelets = []
        for pagelet in self.__pagelets:
            pending_pagelets.extend(pagelet.write_in_place(
                buffer,
                placeholder_index_factory=placeholder_index_factory,
            ))
        return pending_pagelets

    def is_content_loaded(self):
        return True

class PageletWriter(object):
    def __init__(self):
        self.__placeheld_pagelets = set()
        self.__next_pagelet_placeholder_index = 0
        self.__pagelet_placeholder_indexes = {}

    def __pagelet_placeholder_index(self, pagelet):
        placeholder_index = self.__pagelet_placeholder_indexes.get(pagelet)
        if placeholder_index is not None:
            return placeholder_index
        placeholder_index = self.__next_pagelet_placeholder_index
        self.__next_pagelet_placeholder_index += 1
        self.__pagelet_placeholder_indexes[pagelet] = placeholder_index
        return placeholder_index

    def write_pagelet_in_place(self, pagelet, buffer):
        pending_pagelets = pagelet.write_in_place(buffer, placeholder_index_factory=self.__pagelet_placeholder_index)
        for pagelet in pending_pagelets:
            self.__placeheld_pagelets.add(pagelet)

    def write_fixups(self, buffer):
        for pagelet in list(self.__placeheld_pagelets):
            if pagelet.is_content_loaded():
                placeholder_index = self.__pagelet_placeholder_indexes[pagelet]
                pending_pagelets = pagelet.write_fixup(buffer, placeholder_index=placeholder_index, placeholder_index_factory=self.__pagelet_placeholder_index)
                self.__placeheld_pagelets.remove(pagelet)
                for pagelet in pending_pagelets:
                    self.__placeheld_pagelets.add(pagelet)

    def has_pending_fixups(self):
        return bool(self.__placeheld_pagelets)

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

        pagelet_writer = PageletWriter()
        encoding = 'utf-8'
        buffer = self.wfile
        buffer.write('''<!DOCTYPE html>
<html>
<head>
<title>Pagelets example</title>
<meta charset=utf-8>
</head>
<body>
<h1>Pagelets example</h1>
<p>This page demonstrates pagelets.
'''.encode(encoding))

        for _ in range(100):
            # HACK(strager): Some browsers like Firefox have
            # a minimum number of bytes before progressive
            # rendering kicks in.
            buffer.write('<span></span>'.encode(encoding))

        pagelet_a = LiteralHTMLPagelet('''<h2>Pagelet A</h2>
<p>This is pagelet A.''')
        pagelet_writer.write_pagelet_in_place(pagelet_a, buffer)

        pagelet_b_1 = TriggeredHTMLPagelet(LiteralHTMLPagelet('<li value=1>Pagelet B 1'))
        pagelet_b_2 = TriggeredHTMLPagelet(LiteralHTMLPagelet('<li value=2>Pagelet B 2'))
        pagelet_b_3 = TriggeredHTMLPagelet(LiteralHTMLPagelet('<li value=3>Pagelet B 3'))
        pagelet_b = TriggeredHTMLPagelet(MultiHTMLPagelet([
            LiteralHTMLPagelet('''<h2 id=pagelet_b>Pagelet B</h2>
<script>console.log(document.getElementById('pagelet_b') ? 'OK' : 'FAIL');</script>
<style>p { font-style: italic; }</style>
<p>This is pagelet B containing three subpagelets:
<ol>'''),
            pagelet_b_1,
            pagelet_b_2,
            pagelet_b_3,
            LiteralHTMLPagelet('''</ol>'''),
        ]))
        pagelet_writer.write_pagelet_in_place(pagelet_b, buffer)

        pagelet_c = LiteralHTMLPagelet('''<h2>Pagelet C</h2>
<p>This is pagelet C.''')
        pagelet_writer.write_pagelet_in_place(pagelet_c, buffer)

        buffer.flush()
        time.sleep(3)

        pagelet_b.set_loaded()
        pagelet_writer.write_fixups(buffer)

        buffer.flush()
        time.sleep(1)

        pagelet_b_3.set_loaded()
        pagelet_writer.write_fixups(buffer)
        pagelet_b_2.set_loaded()
        pagelet_writer.write_fixups(buffer)

        buffer.flush()
        time.sleep(1)

        pagelet_b_1.set_loaded()
        pagelet_writer.write_fixups(buffer)

        if pagelet_writer.has_pending_fixups():
            raise Exception('Fixups not all resolved')

        self.wfile.write('''</body>
</html>
'''.encode(encoding))

def main():
    address = ('', 8000)
    http_server = http.server.HTTPServer(address, RequestHandler)
    logger.info('Serving on address: %s:%d', *http_server.server_address)
    http_server.serve_forever()

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main()
