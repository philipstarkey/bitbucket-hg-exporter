# Copyright 2019 Philip Starkey
#
# This file is part of bitbucket-hg-exporter.
# https://github.com/philipstarkey/bitbucket-hg-exporter
#
# bitbucket-hg-exporter is distributed under a custom license.
# See the LICENSE file in the GitHub repository for further details.

import os
from setuptools import setup, find_packages

# Define the current version of the library
# Update this before building and publishing a new version
# see https://semver.org/ for guidelines on how to modify the version string
VERSION = '0.1.0'

# get directory of setup.py and the rest of the code for the library
code_dir = os.path.abspath(os.path.dirname(__file__))

# Auto generate a __version__ file for the package to import
with open(os.path.join(code_dir, 'bitbucket_hg_exporter', '__version__.py'), 'w') as f:
    f.write("__version__ = '%s'\n" % VERSION)

# Work around the fact that the readme.md file doesn't exist for users installing
# from the tar.gz format. However, in this case, they won't be uploading to PyPi
# so they don't need it!
try:
    # Read in the readme file as the long description
    with open(os.path.join(code_dir, 'README.md')) as f:
        long_description = f.read()
except Exception:
    long_description = ""

# get a list of all GH
datafiles = []
def find_datafiles(dir):
    df = []
    for f in os.listdir(dir):
        if os.path.isdir(os.path.join(dir,f)):
            find_datafiles(os.path.join(dir,f))
        else:
            df.append(os.path.join(dir,f).replace(code_dir+os.sep, ''))
    if df:
        datafiles.append((dir.replace(code_dir+os.sep, ''),df))


find_datafiles(os.path.join(code_dir, 'bitbucket_hg_exporter', 'gh-pages-template'))


setup(
    name='bitbucket_hg_exporter',
    version=VERSION,
    description='A tool for exporting all project data from a BitBucket mercurial repository',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/philipstarkey/bitbucket-hg-exporter',
    project_urls={
        "Source Code": "https://github.com/philipstarkey/bitbucket-hg-exporter",
    },
    author='Philip Starkey',
    classifiers=['Development Status :: 3 - Alpha',
                 'Programming Language :: Python :: 3.7',
                 'Environment :: Console',
                 'Intended Audience :: Developers',
                 'License :: Other/Proprietary License',
                 'Natural Language :: English',
                 'Operating System :: OS Independent',
                ],
    python_requires='>=3.7, <4',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'keyring',
        'requests',
        'questionary',
        'python-dateutil',
        'colorama',
    ],
    data_files=datafiles,
)