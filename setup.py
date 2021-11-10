from glob import glob
from setuptools import find_packages
from setuptools import setup

import distutils.cmd
import distutils.log
import os
import re
import subprocess

SETUP_REQUIRES = ['setuptools_scm']
INSTALL_REQUIRES = ['pyyaml', 'sortedcontainers', 'termcolor', 'aiorwlock']
DEVELOP_REQUIRES = ['autopep8', 'isort', 'mypy', 'pylint', 'sphinx', 'sphinx_rtd_theme', 'tox']
TESTS_REQUIRE = ['pytest', 'pytest-cov', 'testfixtures']  # TODO: Replicated in tox.ini


def readme():
    sphinx = re.compile(':py:[a-z]+:(`[^`]+`)')
    with open('README.rst') as readme_file:
        return sphinx.sub('`\\1`', readme_file.read())


class SimpleCommand(distutils.cmd.Command):
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        subprocess.check_call(self.command)


class AllCommand(SimpleCommand):
    description = 'run all needed steps before commit'

    def run(self):
        self.run_command('no_unknown_files')
        self.run_command('no_todo' + 'x')
        self.run_command('is_formatted')
        self.run_command('pylint')
        self.run_command('mypy')
        self.run_command('check')
        self.run_command('build')
        self.run_command('pytest')
        self.run_command('tox')
        self.run_command('html')


class CleanCommand(SimpleCommand):
    description = 'remove all generated files and directories'
    command = ['tools/clean']


class HtmlCommand(SimpleCommand):
    description = 'run sphinx to generate HTML documentation'
    command = ['tools/generate_documentation']


class IsformattedCommand(SimpleCommand):
    description = 'use autopep8 and isort to check the formatting of all Python source files'
    command = ['tools/is_formatted']


class MypyCommand(SimpleCommand):
    description = 'run MyPy on all Python source files'
    command = ['mypy',
               '--warn-redundant-casts',
               '--disallow-untyped-defs',
               '--warn-unused-ignores',
               '--scripts-are-modules',
               *glob('dynamake/**/*.py', recursive=True),
               *glob('tests/**/*.py', recursive=True),
               *glob('bin/**/*.py', recursive=True)]


class PyTestCommand(SimpleCommand):
    description = 'run pytest and generate coverage reports'
    command = ['pytest',
               '--cov=dynamake',
               '--cov-report=html',
               '--cov-report=term',
               '--no-cov-on-fail']

    def run(self):
        if os.path.exists('.coverage'):
            os.remove('.coverage')
        super().run()


class NoTodo_xCommand(SimpleCommand):
    description = 'ensure there are no leftover TODO' + 'X in the source files'
    command = ['tools/no_todo_x']


class PylintCommand(SimpleCommand):
    description = 'run Pylint on all Python source files'
    command = [
        'pylint',
        '--init-import=yes',
        '--ignore-imports=yes',
        '--disable=' + ','.join([
            'bad-continuation',
            'bad-whitespace',
            'fixme',
            'global-statement',
            'no-member',
            'too-few-public-methods',
            'ungrouped-imports',
            'unsubscriptable-object',
            'wrong-import-order',
        ])
    ] + [
        path for path
        in glob('dynamake/**/*.py', recursive=True)
        if path != 'dynamake/version.py'
    ] + glob('tests/**/*.py', recursive=True)


class ReformatCommand(SimpleCommand):
    description = 'use autopep8 and isort to fix the formatting of all Python source files'
    command = ['tools/reformat']


class NoUnknownFilesCommand(SimpleCommand):
    description = 'ensure there are no source files git is not aware of'
    command = ['tools/no_unknown_files']


class ToxCommand(SimpleCommand):
    description = 'run tests in a virtualenv using Tox'
    command = ['tox']


setup(name='dynamake',
      use_scm_version=dict(write_to='dynamake/version.py'),
      description='Dynamic Make in Python',
      long_description=readme(),
      long_description_content_type='text/x-rst',
      classifiers=[
          'Development Status :: 3 - Alpha',
          'License :: OSI Approved :: MIT License',
          'Programming Language :: Python :: 3.7',
          'Programming Language :: Python :: 3.8',
          'Programming Language :: Python :: 3.9',
          'Topic :: Software Development :: Build Tools',
          'Intended Audience :: Developers',
      ],
      keywords='make',
      url='https://github.com/orenbenkiki/dynamake.git',
      author='Oren Ben-Kiki',
      author_email='oren@ben-kiki.org',
      license='MIT',
      packages=find_packages(exclude=['tests']),
      package_data={'dynamake': ['py.typed']},
      entry_points={'console_scripts': [
          'dynamake=dynamake.__main__:main',
      ]},
      # TODO: Replicated in tox.ini
      setup_requires=SETUP_REQUIRES,
      install_requires=INSTALL_REQUIRES,
      tests_require=TESTS_REQUIRE,
      extras_require={  # TODO: Is this the proper way of expressing these dependencies?
          'develop': INSTALL_REQUIRES + TESTS_REQUIRE + DEVELOP_REQUIRES
      },
      cmdclass={
          # TODO: Add coverage command (if it is possible to get it to work).
          'all': AllCommand,
          'clean': CleanCommand,
          'html': HtmlCommand,
          'is_formatted': IsformattedCommand,
          'mypy': MypyCommand,
          'pytest': PyTestCommand,
          'pylint': PylintCommand,
          'reformat': ReformatCommand,
          'no_todo' + 'x': NoTodo_xCommand,
          'no_unknown_files': NoUnknownFilesCommand,
          'tox': ToxCommand,
      })
