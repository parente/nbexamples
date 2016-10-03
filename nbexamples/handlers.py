"""Tornado handlers for nbexamples web service."""

import os
import shutil
import subprocess as sp
import json
import glob
import pwd

from tornado import web

import nbformat
from notebook.utils import url_path_join as ujoin
from notebook.base.handlers import IPythonHandler
from traitlets import Unicode
from traitlets.config import LoggingConfigurable


class Examples(LoggingConfigurable):
    reviewed_example_dir = Unicode('', config=True, help='Directory of reviewed notebooks, relative to NotebookApp.notebook_dir')
    unreviewed_example_dir = Unicode('', config=True, help='Directory of unreviewed notebooks, relative to NotebookApp.notebook_dir')

    def _reviewed_example_dir_default(self):
        return self.parent.notebook_dir

    def _unreviewed_example_dir_default(self):
        return self.parent.notebook_dir

    def list_examples(self):
        categories = ['reviewed', 'unreviewed']
        dirs = [self.reviewed_example_dir, self.unreviewed_example_dir]
        all_examples = []
        uid = os.getuid()
        for category, d in zip(categories, dirs):
            filepaths = glob.glob(os.path.join(d, '*.ipynb'))
            examples = [{'filepath': os.path.abspath(fp)} for fp in filepaths]
            for example in examples:
                node = nbformat.read(example['filepath'], nbformat.NO_CONVERT)
                st = os.stat(example['filepath'])
                try:
                    user = pwd.getpwuid(st.st_uid)
                except KeyError:
                    example['user'] = None
                else:
                    example['user'] = user.pw_gecos or user.pw_name
                example['datetime'] = st.st_mtime
                example['filename'] = os.path.basename(example['filepath'])
                example['metadata'] = node.metadata
                example['category'] = category
                example['owned'] = st.st_uid == uid
            all_examples.extend(examples)
        return all_examples

    def fetch_example(self, example_id, dest):
        abs_dest = os.path.join(self.parent.notebook_dir, dest)
        if not abs_dest.endswith('.ipynb'):
            abs_dest += '.ipynb'
        # Make a copy of the example notebook, stripping output.
        p = sp.Popen(['jupyter', 'nbconvert', example_id,
                      '--Exporter.preprocessors=["nbexamples.strip_output.StripOutput"]',
                      '--to', 'notebook', '--output', abs_dest],
                     stdout=sp.PIPE, stderr=sp.PIPE)
        output, err = p.communicate()
        retcode = p.poll()
        if retcode != 0:
            raise RuntimeError('jupyter nbconvert exited with error {}'.format(
                               err))

    def submit_example(self, user_filepath):
        # Make a copy of the example notebook.
        src = os.path.join(self.parent.notebook_dir, user_filepath)
        filename = os.path.basename(user_filepath)
        dest = os.path.join(self.unreviewed_example_dir, filename)
        shutil.copyfile(src, dest)
        return dest

    def preview_example(self, filepath):
        fp = filepath  # for brevity
        if not os.path.isfile(fp):
            raise web.HTTPError(404, "Example not found: %s" % fp)
        p = sp.Popen(['jupyter', 'nbconvert', '--to', 'html', '--stdout', fp],
                     stdout=sp.PIPE, stderr=sp.PIPE)
        output, _ = p.communicate()
        retcode = p.poll()
        if retcode != 0:
            raise RuntimeError('nbconvert exited with code {}'.format(retcode))
        return output.decode()

    def delete_example(self, filepath):
        os.remove(filepath)


class BaseExampleHandler(IPythonHandler):

    @property
    def manager(self):
        return self.settings['example_manager']


class ExamplesHandler(BaseExampleHandler):

    @web.authenticated
    def get(self):
        self.finish(json.dumps(self.manager.list_examples()))


class ExampleActionHandler(BaseExampleHandler):

    @web.authenticated
    def get(self, action):
        example_id = self.get_argument('example_id')
        if action == 'preview':
            self.finish(self.manager.preview_example(example_id))
        elif action == 'fetch':
            dest = self.get_argument('dest')
            self.manager.fetch_example(example_id, dest)
            # nbconvert appends '.ipynb' if it isn't present
            if not dest.endswith('.ipynb'):
                dest += '.ipynb'
            self.redirect(ujoin(self.base_url, 'notebooks', dest))
        elif action == 'submit':
            dest = self.manager.submit_example(example_id)
            self.redirect(ujoin(self.base_url, 'tree#examples' + dest))
        elif action == 'delete':
            self.manager.delete_example(example_id)
            self.redirect(ujoin(self.base_url))


# -----------------------------------------------------------------------------
# URL to handler mappings
# -----------------------------------------------------------------------------


_example_action_regex = r"(?P<action>fetch|preview|submit|delete)"

default_handlers = [
    (r"/examples", ExamplesHandler),
    (r"/examples/%s" % _example_action_regex, ExampleActionHandler),
]


def load_jupyter_server_extension(nbapp):
    """Load the nbserver"""
    webapp = nbapp.web_app
    webapp.settings['example_manager'] = Examples(parent=nbapp)
    base_url = webapp.settings['base_url']

    ExampleActionHandler.base_url = base_url  # used to redirect after fetch
    webapp.add_handlers(".*$", [
        (ujoin(base_url, pat), handler)
        for pat, handler in default_handlers
    ])
