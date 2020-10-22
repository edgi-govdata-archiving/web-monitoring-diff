import glob
import re
from pathlib import Path
from setuptools import setup, find_packages
import sys

import versioneer


min_version = (3, 7)
if sys.version_info < min_version:
    raise RuntimeError("Python version is {}. Requires 3.7 or greater."
                       "".format(sys.version_info))


def read(fname):
    with open(Path(__file__).parent / fname) as f:
        result = f.read()
    return result


# Delimits a setup.py-compatible requirement name/version from the extras that
# only pip supports (environment info, CLI options, etc.).
# https://pip.pypa.io/en/stable/reference/pip_install/#requirements-file-format
REQUIREMENT_DELIMITER = re.compile(r';|--')


def cleanup(line):
    '''
    Convert a pip requirements file line into an install_requires-style line.
    '''
    return REQUIREMENT_DELIMITER.split(line, 1)[0]


# We maintain our required dependencies in separate files because:
# - Some requirements rely on special pip features that can't be specified with
#   `install_requires`, like `git` URLs or the `--no-binary` flag.
# - When building Docker containers, it’s helpful to be able to layer
#   dependencies before the actual package code, so changes to the package
#   code can still utilize Docker's cache for faster builds.
def read_requirements(fname):
    return [cleanup(r) for r in read(fname).splitlines()
            if not r.startswith('git+https://') and not r.startswith('#')]


setup(
    name='web-monitoring-diff',
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    description='Tools for diffing & comparing web content, including a web '
                'server that generates those diffs as an HTTP service.',
    long_description=read('README.md'),
    long_description_content_type='text/markdown',
    author="Environmental Data Governance Initiative",
    author_email='EnviroDGI@protonmail.com',
    url='https://github.com/edgi-govdata-archiving/web-monitoring-diff',
    python_requires='>={}'.format('.'.join(str(n) for n in min_version)),
    packages=find_packages(exclude=['docs', 'tests']),
    # NOTE: when updating this, make sure to update MANIFEST.in as well!
    package_data={'web_monitoring_diff': ['example_data/*']},
    # TODO: migrate to entry_points, which is recommended.
    scripts=glob.glob('scripts/*'),
    license="GPL-3.0-only",
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
    ],
    install_requires=read_requirements('requirements.txt'),
    extras_require={
        'server': read_requirements('requirements-server.txt'),
        'dev': read_requirements('requirements-dev.txt'),
    },
)
