from __future__ import print_function

import os
import pstats
import sys
import traceback
import time
import webbrowser
import fnmatch
import threading
import argparse
import json
from six import StringIO, iteritems, itervalues
import tornado.ioloop
import tornado.web
from collections import defaultdict, deque
from itertools import groupby

from openmdao.devtools.iprofile import process_profile, profile_py_file
from openmdao.devtools.iprof_utils import func_group, find_qualified_name, _collect_methods


def launch_browser(port):
    time.sleep(1)
    webbrowser.get().open('http://localhost:%s' % port)

def startThread(fn):
    thread = threading.Thread(target=fn)
    thread.setDaemon(True)
    thread.start()
    return thread

def _parent_key(d):
    parts = d['id'].rsplit('-', 1)
    if len(parts) == 1:
        return ''
    return parts[0]

def stratify(call_data, sortby='time'):
    """
    Group node data by depth and sort with a depth by time.
    """
    depth_groups = []
    node_list = []  # all nodes in a single list
    depthfunc=lambda d: d['depth']
    for key, group in groupby(sorted(call_data.values(), key=depthfunc), key=depthfunc):
        # now further group each group by parent, then sort those in descending order
        # by 'sortby'
        depth_groups.append({
            key: sorted(sub, key=lambda d: d[sortby], reverse=True)
                for key, sub in groupby(sorted(group, key=_parent_key), key=_parent_key)
        })

    max_depth = len(depth_groups)
    delta_y = 1.0 / max_depth
    y = 0
    max_x = call_data['$total'][sortby]

    for depth, pardict in enumerate(depth_groups):
        y0 = delta_y * depth
        y1 = y0 + delta_y

        for parent, children in iteritems(pardict):
            if not parent:
                end_x = 0
            else:
                end_x = call_data[parent]['x0'] * max_x

            for i, node in enumerate(children):
                start_x = end_x
                end_x += node[sortby]
                node['x0'] = start_x / max_x
                node['x1'] = end_x / max_x
                node['y0'] = y0
                node['y1'] = y1
                node['idx'] = len(node_list)
                node_list.append(node)

        # values = [(dat['x0'], dat['x1']) for dat in group[:3]]
        # print("depth", depth, "data:", len(group), 'values:', values)

    return depth_groups, node_list


class Application(tornado.web.Application):
    def __init__(self, options):
        self.call_data, _ = process_profile(options.files)
        self.depth_groups, self.node_list = stratify(self.call_data)
        self.options = options

        # create a new data structure that is a dict keyed on root pathname,
        # where each value is a list of lists of node data stored by depth (index==depth).
        self.call_tree = tree = defaultdict(lambda : [None, {}])
        for path, data in iteritems(self.call_data):
            data['id'] = path
            parts = path.rsplit('-', 1)
            # add our node to our parent
            if len(parts) > 1:
                tree[parts[0]][1][path] = data
            tree[path][0] = data

        handlers = [
            (r"/", Index),
            (r"/func/([0-9]+)", Function),
        ]

        settings = dict(
             template_path=os.path.join(os.path.dirname(__file__), "templates"),
             static_path=os.path.join(os.path.dirname(__file__), "static"),
        )

        super(Application, self).__init__(handlers, **settings)

    def get_nodes(self, idx):
        """
        Yield all children of the given root up to a depth of root depth + depth.
        """
        if idx == 0:
            root = self.call_tree['$total']
        else:
            root = self.node_list[idx]
            root = self.call_tree[root['id']]

        maxcalls = self.options.maxcalls
        stack = deque()
        stack.appendleft(root)
        callcount = 1
        stop_adding = False
        while stack:
            parent, children = stack.pop()
            yield parent
            if not stop_adding:
                callcount += len(children)
                if callcount <= maxcalls:
                    for child in itervalues(children):
                        stack.appendleft(self.call_tree[child['id']])
                else:
                    stop_adding = True


class Index(tornado.web.RequestHandler):
    def get(self):
        app = self.application
        self.render("iprofview.html", title="My title")


class Function(tornado.web.RequestHandler):
    def get(self, idx):
        print("func: %s" % idx)
        app = self.application
        dump = json.dumps(list(app.get_nodes(int(idx))))
        self.set_header('Content-Type', 'application/json')
        self.write(dump)


def prof_view():
    """
    Called from a command line to generate an html viewer for profile data.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port', action='store', dest='port',
                        default=8009, type=int,
                        help='port used for web server')
    parser.add_argument('--noshow', action='store_true', dest='noshow',
                        help="Don't pop up a browser to view the data.")
    parser.add_argument('-t', '--title', action='store', dest='title',
                        default='Profile of Method Calls by Instance',
                        help='Title to be displayed above profiling view.')
    parser.add_argument('-g', '--group', action='store', dest='group',
                        default='openmdao',
                        help='Determines which group of methods will be tracked. Current '
                             'options are: %s and "openmdao" is the default' %
                              sorted(func_group.keys()))
    parser.add_argument('-m', '--maxcalls', action='store', dest='maxcalls',
                        default=5000, type=int,
                        help='Maximum number of calls displayed at one time.  Default=100.')
    parser.add_argument('files', metavar='file', nargs='+',
                        help='Raw profile data files or a python file.')

    options = parser.parse_args()

    if options.files[0].endswith('.py'):
        if len(options.files) > 1:
            print("iprofview can only process a single python file.", file=sys.stderr)
            sys.exit(-1)
        profile_py_file(options.files[0], methods=func_group[options.group])
        options.files = ['iprof.0']

    app = Application(options)
    app.listen(options.port)

    print("starting server on port %d" % options.port)

    serve_thread  = startThread(tornado.ioloop.IOLoop.current().start)
    #launch_thread = startThread(lambda: launch_browser(options.port))

    while serve_thread.isAlive():
        serve_thread.join(timeout=1)


if __name__ == '__main__':
    cmd_view_pstats()
