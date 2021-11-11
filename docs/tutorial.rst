Introduction
============

This tutorial walks through the features of `DynaMake <https://pypi.org/project/dynamake/>`_, a
`make <https://en.wikipedia.org/wiki/Make_(software)>`_-like build tool implemented in the popular
`Python <https://www.python.org/>`_ programming language, loosely inspired by the `shake
<https://shakebuild.com/>`_ build system which is implemented in the `Haskell
<https://www.haskell.org/>`_ programming language.

.. note::

    This only covers the main features of DynaMake. For a full list of available functionality,
    see the reference documentation of the :py:mod:`dynamake` module.

DynaMake is essentially a Python library. There is a ``dynamake`` universal executable script
provided with the package, similar to `SCons <https://scons.org/>`_. You can also invoke DynaMake as
``python -m dynamake`` if you prefer. You still need to write your build script in Python, using the
library's utilities, and you can also easily invoke the provided ``make`` function from your own
custom main function.

DynaMake build steps may invoke programs written in any language, either directly or by invoking
shell commands, similarly to any other build tool.

Build Scripts
-------------

A typical build script consists of a set of step functions, which are functions
decorated with :py:func:`dynamake.step`. This requires an explicit
``output=...`` parameter listing the file(s) created by the step.

Here is a DynaMake build script which copies the file ``foo`` to the file
``bar``, if ``bar`` does not exist, or if ``foo`` is newer than ``bar``:

.. code-block:: python

    from dynamake import *

    @step(output='foo')
    async def copy_bar_to_foo() -> None:
        require('bar')
        await shell('cp bar foo')

This is essentially equivalent to the ``make`` rule:

.. code-block:: make

    foo: bar
            cp bar foo

That is, DynaMake will only execute the shell command ``cp bar foo`` if the
``foo`` file is missing or is older than the ``bar`` file.

In general, DynaMake will execute the step that produces the requested output (by default, ``all``).
Invoking ``require`` will append to the list of dependencies, which will be built before executing
any action (here, ``await shell(...)``), or when explicitly invoking ``await sync()``.

In general, DynaMake will skip actions unless it finds a sufficient reason to execute them. If there
are multiple actions in a step, and DynaMake skipped some to discover that a later action needs to
be executed, then DynaMake restarts the step, and this time executes all actions. That is, step
functions should be "idempotent"; re-running a step multiple times should in principle have no
effect (other than to modify the creation or last modification time of the output files).

* Invoke :py:func:`dynamake.require` to ensure the specified dependency exists and is and
  up-to-date. Building of required input files is done asynchronously (concurrently, possibly in
  parallel).

* Invoke ``await`` of :py:func:`dynamake.sync` to ensure all required input files specified so far
  have completed to build.

* Invoke ``await`` of :py:func:`dynamake.shell` or :py:func:`dynamake.spawn` to trigger the
  execution of a shell command or an arbitrary external program. This will automatically ``sync``
  first to ensure all required input files have completed to build.

.. note::

   **Inside a step, do not simply ``await`` co-routines that are not provided by DynaMake.**

   DynaMake tracks the current step, and invoking ``await`` of some other co-routines will confuse
   it. Use :py:func:`dynamake.done` to ``await`` on external co-routines. That is, write ``await
   done(something())`` rather than ``await something()``.

* Use Python code to examine the file system, analyze the content of dependencies (following a
  ``sync``), perform control flow operations (branches, loops), invoke Python functions which do any
  of these things, etc. It is recommended to use :py:class:`dynamake.Stat` for ``stat`` operations,
  as these are efficiently cached by DynaMake which results in faster builds.

.. note::

    **The correctness of the ``stat`` cache depends on accurate listing of each action's inputs and
    outputs.**

    In general DynaMake needs these lists to be accurate for correct operation. This is true of
    almost any build tool. In theory, one could use ``strace`` to automatically extract the true
    lists of inputs and outputs, but this is complex, fragile, and impacts the performance.

The ability to mix general Python code together with ``make`` functionality is what gives DynaMake
its additional power over static build tools like ``make`` or ``ninja``. The following examples will
demonstrate some common idioms using this power.

Pattern Steps
-------------

A build step may be used to produce any of a set of outputs. For example:

.. code-block:: python

    from dynamake import *

    @step(output='{*name}.o')
    async def compile_object(name: str) -> None:
        require(f'{name}.c')
        await shell('cc -o {name}.o {name}.c')

Which is essentially equivalent to the ``Makefile``:

.. code-block:: make

    %.o: %.c
            cc -p $*.o $*.c

That is, this will allow DynaMake to compile any file with a ``.c`` suffix into a file with a ``.o``
suffix. In general, DynaMake allows outputs to contains multiple :py:class:`dynamake.Captured`
patterns, as opposed to ``make`` which only allows a single ``%`` in the rule. Each of the named
patterns must be also specified as a string parameter to the step function. This allows the captured
parts of the output name to be used in constructing the names of dependencies and/or actions to
perform (similar but more powerful from ``$*`` in ``make``). For example:

.. code-block:: python

    from dynamake import *

    CCFLAGS = dict(debug='-g', release='-o3')

    @step(output='{*mode}/{*name}.o')
    async def compile_object(name: str) -> None:
        require(f'{name}.c')
        await shell('cc {CCFLAGS[mode]} -o {name}.o {name}.c')

Will allow DynaMake to compile each file with a ``.c`` suffix into an object file with a ``.o``
suffix inside either the ``debug`` or ``release`` sub-directories. There is no simple equivalent
for this in ``make`` (or similar tools).

DynaMake provides the :py:func:`dynamake.inputs`, :py:func:`dynamake.input`,
:py:func:`dynamake.outputs` and :py:func:`dynamake.output` functions to access the name of the
required input(s) and produced output(s) of a step. For example, the above could have been written
as:

.. code-block:: python

    from dynamake import *

    CCFLAGS = dict(debug='-g', release='-o3')

    @step(output='{*mode}/{*name}.o')
    async def compile_object(name: str) -> None:
        require(f'{name}.c')
        await shell('cc {CCFLAGS[mode]} -o {output()} {input()}')

This allows avoiding repetition of the output and required dependency file names, though it requires
care in case there are multiple outputs and/or dependencies.

Dynamic Inputs
--------------

A build step may dynamically compute the set of dependencies based on the content of a subset of
these dependencies. For example:

.. code-block:: python

    from dynamake import *
    from c_source_files import scan_included_files  # Assume this for simplicity.


    @step(output='{*name}.o')
    async def compile_object(name: str) -> None:
        require_file_and_includes(f'{name}.c')
        await shell(f'cc -o {output()} {input()}')


    # Naive: does not handle a cycle of files including each other, does not allow for missing
    # include files (e.g. in #ifdef), doesn't cache results, etc.
    def require_file_and_includes(paths: *Strings) -> None:
        require(*paths)  # Mark source/header file(s) as a dependency.
        await sync()  # Ensures all specified source/header file(s) are up-to-date.

        for path in each_string(*paths):
            # Add as dependencies all files included from the given source/header file(s).
            require_file_and_includes(scan_included_files(path))

The above approach generalizes to any case where the content of some of the dependencies determines
the full list of dependencies, and allows for multiple stages of dependency computation. It is also
possible to explicitly cache the dependencies in a file, for example using ``gcc -MM``:

.. code-block:: python

    from dynamake import *
    import os


    @step(output='{*name}.o')
    async def compile_object(name: str) -> None:
        require_file_and_includes(f'{name}.c')
        await shell(f'cc -o {output()} {input()}')


    def require_file_and_includes(paths: *Strings) -> None:
        require([f'{path}.depends' for path in each_string(*paths)])
        await sync()
        for path in each_string(*paths):
            require(read_depends(path))


    @step(output='{name}.depends')
    async def collect_depends(name: str) -> None:
        require(name)
        if os.path.exist(f'{name}.depends'): # Will not exist in 1st build.
            require(read_depends(name))
        await shell(f'gcc -MM {input()} > {output()}')


    def read_depends(path: str) -> List[str]:
        return open(f'{path}.depends').read().split()[1:]

Which is similar to the ``Makefile`` idiom:

.. code-block:: make

    SRCS := ...

    depends: $(SRCS)
        gcc -MM $(SRCS) > depends

    include depends

Except that this requires listing all the source files up-front, and will re-scan all of them if any
of them has changed, while the DynaMake solution does not require listing all source files up-front
and will only re-scan source files which actually changed.

Collections of Strings
----------------------

The previous example demonstrates the use of the :py:const:`dynamake.Strings` type. Many DynaMake
functions take one or more "strings or list of (strings or list of (strings or ...))" - a type which
is impossible to express in Python's ``mypy`` type system, so is only approximated here. This makes
it possible for :py:func:`dynamake.require` to accept a single string argument, multiple string
arguments, a list of string arguments (returned by ``read_depends`` above), etc.

To help deal with this type, DynaMake provides the :py:func:`dynamake.each_string` and
:py:func:`dynamake.flatten` functions, which allow iteration on arbitrary ``Strings`` and converting
them to a simple flat list of strings for further processing.

Annotated Strings
-----------------

DynaMake allows attaching annotations (:py:class:`dynamake.AnnotatedStr`) to strings (and patterns).
Multiple annotations may be applied to the same string. The provided string processing functions
preserve these (that is, pass the annotations from the input(s) to the output(s)). These annotations
are used by DynaMake to modify the handling of required and output files.

Phony Outputs
.............

A :py:func:`dynamake.phony` output is used to force the creation of a collection of files, without
being one. The default ``all`` target is typically a phony target:

.. code-block:: python

    import * from dynamake

    @step(output=phony(all))
    def all() -> None:
        require('some', 'files')

Which is essentially equivalent to the ``Makefile``:

.. code-block:: make

    .PHONY: all
    all: some files

A common pattern in ``make`` is to build the list of dependencies of a phony target
using multiple rules:

.. code-block:: make

    .PHONY: all
    all: some
    all: files

In DynaMake, this requires using a global variable:

.. code-block:: python

    import * from dynamake

    ALL = []

    @step(output=phony(all))
    def all() -> None:
        require(ALL)

    ALL += ['some']
    ALL += ['files']

Similar to ``make``, when a step has any ``phony`` output(s), its actions are always executed.
Unlike ``make``, steps that require the phony output as a dependency are *not* always rebuilt.
Instead, a synthetic modification time is assigned to the phony output: one nanosecond newer than
the newest required input. Therefore steps depending on the phony output will only rebuild their
outputs if an actual real dependency has been modified since the last build.

Phony Action Parameters
-----------------------

If using persistent state to track actions (see below), this state will ignore any parts of invoked
commands that are marked as :py:func:`dynamake.phony`. This prevents changes to irrelevant command
line options from triggering a rebuild. For example, the following:

.. code-block:: python

    import * from dynamake
    import os

    @step(output='foo')
    def foo() -> None:
        require('bar')
        await shell('make_foo --jobs', phony(str(os.cpu_count())), 'bar')

Will not trigger a rebuild of ``foo`` if running on a machine with a different number of CPUs.

Optional Outputs and Dependencies
---------------------------------

If an output is annotated as :py:func:`dynamake.optional`, then DynaMake will not complain if it
doesn't exist when the step's actions complete. If, in addition, the step requiring the dependencies
also annotated it as ``optional``, then DynaMake will allow it to proceed even if the dependency was
not created. If either the producer or the consumer of the file does not annotate it as
``optional``, then the build will fail.

For example:

.. code-block:: python

    import * from dynamake
    import os

    @step(output=['results.txt', optional('warnings.txt')]
    async def compute() -> None:
        await shell('compute ...')

    @step(output='warnings.html')
    async def warnings() -> None:
        require(optional('warnings.txt'))
        await sync()
        if os.path.exist('warnings.txt'):
            await shell('htmlize < {input()} > {output()}}')
        else:
            require('no_warnings.html')
            await shell('cp {input(-1)} {output()}')

Exists Outputs and Inputs
-------------------------

If an output or a dependency is annotated as :py:func:`dynamake.exists`, then DynaMake will ignore
its modification time and only considers whether the file exists or not. That is,
``require(exists(dependency))`` will trigger rebuilding the dependency if it does not exist, but
will not rebuild it if it exists regardless of the modification time of its dependencies. Specifying
``output=exists(target)`` instructs DynaMake to skip updating the modification time of the target to
ensure it is newer than all its dependencies, regardless of the setting of
``--touch_success_outputs`` (see below).

For example:

.. code-block:: python

    import * from dynamake
    import os

    @step(output=exists('figures'))
    async def ensure_figures() -> None:
        await shell(f'mkdir -p {output()}')

    @step(output='figures/figure-1.png')
    async def figure_1() -> None:
        require(exists('figures'))
        ...
        await shell(f'create_figure_1 > {output()}')

Precious Outputs
----------------

If an output is annotated as :py:func:`dynamake.precious`, then DynaMake will never remove it, even
if rebuilding it or if the step rebuilding it fails, regardless of the setting of
``--remove_stale_outputs`` and ``--remove_failed_outputs`` (see below).

Multiple Outputs
----------------

A step may produce multiple output files, for example:

.. code-block:: python

    from dynamake import *

    @step(output=['y.tab.c', 'y.tab.h'])
    async def yacc() -> None:
        require('grammar.yacc')
        await shell('yacc -d {input()}')

There is no simple equivalent for this in ``make``.

If the step has pattern outputs, then all the outputs must have the same list of capture patterns.
For example:

.. code-block:: python

    from dynamake import *

    @step(output=['{name}.tab.c', '{name}.tab.h'])
    async def yacc(name: str) -> None:
        require(f'{name}.yacc')
        await shell(f'yacc -d -b {name} {input()}')

Dynamic Outputs
---------------

When a step may produce a dynamic set of outputs, it must specify an ``output`` pattern which
includes some non captured parts (whose name starts with ``_``). For example:

.. code-block:: python

    from dynamake import *

    @step(output=['files/{*name}/{**_file}',
                  'files/{*name}/.all.done')
    async def extract_incoming(name: str) -> None:
        require(f'incoming/{name}.tgz')
        await shell(f'mkdir files/{name}; '
                    f'cd files/{name}; '
                    f'tar xvzf ../../{input()}; '
                    f'touch .all.done')

This will instruct DynaMake that to build any ``files/{name}/{file}``, it needs extract all files
from the matching ``incoming/{name}.tgz``, without knowing in advance which files are contained in
the tar file.

Requiring *any* of the matching output files will cause the step to be invoked and ensure *all*
outputs are up-to-date. A common trick, demonstrated above, it to have an additional final file
serve as a convenient way to require all the files. This allows to query the filesystem for the full
list of files. For example, assume each file needs to be processed, and then all files need to be
collected together:

.. code-block:: python

    @step(output='processed/{*name}/{**file}')
    async def process(name: str, file: str) -> None:
        require(f'files/{name}/{file}')
        awat shell(f'process_file < {input()} > {output()}')

    @step(output='outgoing/{*name}.tgz')
    async def collect_outgoing(name: str) -> None:
        require(f'files/{name}/.all.done')
        await sync()
        all_parts = glob_fmt(f'files/{name}/{{*part}}.txt',
                             f'processed/{name}/{{part}}.txt')
        await shell(f'cd processed/{name}; '
                    f'tar cvzf ../../{output()} .')

There is no simple equivalent for this in ``make`` (or similar tools).

Globbing and Formatting
-----------------------

The :py:func:`dynamake.glob_fmt` function used above performs a ``glob`` of the specified pattern,
captures any ``{*parameters}`` and then uses them to format some templates. This is very useful when
dealing with a dynamic set of files. DynaMake provides other functions to help with ``glob`` of
patterns, such as :py:func:`dynamake.glob_capture`, :py:func:`dynamake.glob_extract` and
:py:func:`dynamake.glob_paths`.

Universal Main Program
----------------------

The easiest way to invoke DynaMake is to place your steps inside ``DynaMake.py`` (or modules
included by ``DynaMake.py``) and invoke the provided ``dynamake`` script (which is equivalent to
running ``python -m dynamake``).

You can specify explicit ``--module`` options in the command line to directly import your step
functions from arbitrary Python modules, instead of the default ``DynaMake.py`` file.

You can also write your own executable script:

.. code-block:: python

    import argparse
    import dynamake as dm
    import my_steps

    dm.make(argparse.ArgumentParser(...))

This will come pre-loaded with your own steps, and allow you to tweak the program's help message and
other aspects, if needed. This is especially useful if you are writing a package that wants to
provide pre-canned steps for performing some complex operation (such as a scientific computation
pipeline).

Finally, you can directly invoke the lower-level API to use build steps as part of your code. See
the implementation of the :py:func:`dynamake.make` function as a starting point.

Control Flags
.............

The behavior of DynaMake can be tweaked by modifying the built-in global parameter values. This is
typically done by specifying the appropriate command line option, which is then handled by the
provided :py:func:`dynamake.make` main function.

* ``--no_actions`` (or ``-n``) instructs DynaMake to not actually execute any actions.
  When an action is specified and needs to be run, DynaMake logs it (in the ``INFO`` or ``FILE`` log
  level) but then stops processing the build step (and any step depending on it). That is, ``-n``
  will only log the first action (or parallel actions) as opposed to the full list of actions needed
  for the build.

  This restriction is because further build code might attempt to directly examine the output from
  the action (e.g., look inside a C file for the list of included headers, look at the list of files
  actually created for a step with dynamic list of outputs, etc.). While this isn't as comprehensive
  as ``make -n`` it still provides some (most?) of its value.

  To make ``-n`` more useful, DynaMake will continue building past "silent" actions, under the
  assumption that such actions perform "insignificant" operations (e.g., creating directories for
  output files) and that subsequent build code does not depend on their results. If this assumption
  fails, the build may fail in strange ways when ``-n`` is specified.

* ``--rebuild_changed_actions`` controls whether DynaMake uses the persistent state to track the
  list of outputs, inputs, invoked sub-steps, and actions with their command line options. This
  ensures that builds are repeatable (barring changes to the environment, such as compiler versions
  etc.). By default this is ``True``.

  Persistent state is kept in YAML files named ``.dynamake/step_name.actions.yaml`` or, for
  parameterized steps, ``.dynamake/step_name/param=value&...&param=value.actions.yaml``. As a
  convenience, this state also includes the start and end time of each of the invoked actions. This
  allows post-processing tools to analyze the behavior of the build script (as an alternative to
  analyzing the log messages).

* ``--failure_aborts_build`` controls whether DynaMake stops the build process on the first failure.
  Otherwise, it attempts to continue to build as many unaffected targets as possible. By default
  this is ``True``.

* ``--remove_stale_outputs`` controls whether DynaMake removes all (non-``precious``) outputs before
  executing the first action of a step. By default this is ``True``.

* ``--touch_success_outputs`` controls whether DynaMake should touch (non-``exists``) output file(s)
  to ensure their modification time is later than that of (non-``exists``) required input files(s).
  By default this is ``False`` because DynaMake uses the nanosecond modification time, which is
  supported on most modern file systems. The modification times on old file systems used a 1-second
  resolution, which could result in the output having the same modification time as the input for a
  fast operation.

  This option might still be needed if an output is a directory (not a file) and is ``precious`` or
  ``--remove_stale_outputs`` is ``False``. In this case, the modification time of a pre-existing
  directory will not necessarily be updated to reflect the fact that output file(s) in it were
  created or modified by the action(s). In general it is not advised to depend on the modification
  time of directories; it is better to specify a glob matching the expected files inside them, or
  use an explicit timestamp file.

* ``--remove_failed_outputs`` controls whether DynaMake should remove (non-``precious``) output
  files when a step action has failed. This prevents corrupt output file(s) from remaining on the
  disk and being used in later invocations or by other programs. By default this is ``True``.

* ``-remove_empty_directories`` controls whether DynaMake will remove empty directories which result
  from removing any output file(s). By default this is ``False``.

* ``--jobs`` controls the maximal number of ``shell`` or ``spawn`` actions that are invoked at the
  same time.

  A value of ``0`` will allow for unlimited number of parallel actions. This is useful if actions
  are to be be executed on a cluster of servers instead of on the local machine, or if some other
  resource(s) are used to restrict the number of parallel actions (see below).

  A positive value will force executing at most this number of parallal actions. For example, a
  value of ``1`` will force executing just one action at a time.

  A negative value will force executing a fraction of the number of logical processors (``nproc``)
  in parallel. For example, ``-1`` will execute at most one action per logical processor, and ``-2``
  will execute at most one action per two logical processors, useful to force executing at most one
  action per physical core on system with two hyper-threads (logical processors) per physical core.

  The default value is ``-1``. You can override this default using the ``DYNAMAKE_JOBS`` environment
  variable.

.. note::

    **The DynaMake python code itself is not parallel.**

    DynaMake always runs on a single process. Parallelism is the result of DynaMake executing an
    external action, and instead of waiting for it to complete, switching over to a different step
    and processing it until it also executes an external action, and so on. Thus actions may execute
    in parallel, while the Python code is still doing only one thing at a time. This greatly
    simplifies reasoning about the code. Specifically, if a piece of code contains no ``await``
    calls, then it is guaranteed to "atomically" execute to completion, so there is no need for a
    lock or a mutex to synchronize between the steps, even when they share some data.

Custom Configuration Flags
..........................

The above control flags are an example of global build configuration parameters. In general, such
parameters have a default, can be overridden by some command line option, and may be used by any
(possibly nested) function of the program.

You can add your own custom configuration parameters. For example:

.. code-block:: python

    import * from dynamake

    mode = Parameter(name='mode', metavar='STR', default='release', parser=str,
                     description='The compilation mode (release or debug).')

    MODE_FLAGS = {
        'debug': [ ... ],
        'release': [ ... ],
    }

    @step(output='obj/{*name}.o')
    async def make_object(name: str) -> None:
        require(f'src/{name}.c')
        await spawn('cc', '-o', output(), MODE_FLAGS[mode.value], input())

That is, constructing a new :py:class:`dynamake.Parameter` specifies the name, default value and
command line option(s) for the parameter. The :py:func:`dynamake.Parameter.value` property is set to
the effective value of the parameter and can be used to modify some step's behavior in arbitrary
ways. This value is either the parameter's default, or the value loaded from the default
``DynaMake.yaml`` configuration file, or the value loaded from another configurtaion file by using
the ``--config``, or the value specified in an explicit command line option for the parameter, in
ascending priority order.

Parallel Resources
..................

As mentioned above, DynaMake will perform all ``require`` operations concurrently, up to the next
``sync`` call of the step (which automatically happens before any ``shell`` or ``spawn`` action). As
a result, by default DynaMake will execute several actions in parallel, subject to the setting of
``--jobs``.

It is possible to define some additional resources using :py:func:`dynamake.resource_parameters` to
restrict parallel execution. For example:

.. code-block:: python

    from dynamake import *

    ram = Parameter(name='ram',
                    short='r',
                    metavar='GB',
                    default=128,
                    parser=str2int(),
                    description='The maximal RAM to use for parallel jobs.')

    resource_parameters(ram=1)  # Specifies default amount of RAM.

    @step(output='foo')
    async def foo() -> None:
        await shell(..., ram=100)

    @step(output='bar')
    async def bar() -> None:
        await shell(..., ram=50)

    @step(output='baz')
    async def baz() -> None:
        require('foo', 'bar') # Will be built serially to avoid using too much RAM.
        await shell(...)  # Uses default amount (1GB) of RAM.

Logging
.......

Complex build scripts are notoriously difficult to debug. To help alleviate this pain, DynaMake uses
the standard Python logging mechanism, and supports the following logging levels:

* ``STDOUT`` and ``STDERR`` print the standard output and standard error of the executed commands,
  annotated with the identification of the step that emitted them. This makes it possible to
  untangle the results of parallel actions.

* ``INFO`` prints only the executed actions. This is similar to the default ``make`` behavior. Use
  this if you just want to know what is being run, when all is well. If ``--log_skipped_actions`` is
  set, then this will also log skipped actions.

* ``FILE`` also print file operations done by DynaMake itself, specifically touching and removing
  files (controlled by the flags ``--touch_success_outputs``, ``--remove_stale_outputs`` and
  ``--remove_failed_outputs``). This gives a more complete picture of the effect DynaMake had on the
  file system.

* ``WHY`` also prints the reason for executing each action (which output file does not exist and
  needs to be created, which input file is newer than which output file, etc.). This is useful for
  debugging the logic of the build script.

* ``TRACE`` also prints each step invocation. This can further help in debugging the logic of the
  build script.

* ``DEBUG`` prints a lot of very detailed information about the flow. Expanded globs, the full list
  of input and output files, the configuration files used, etc. This is useful in the hopefully very
  rare cases when the terse output from the ``WHY`` and ``TRACE`` levels is not sufficient for
  figuring out what went wrong.

The ``FILE``, ``WHY`` and ``TRACE`` levels are not a standard python log level. They are defined to
be between ``INFO`` and ``DEBUG``, in the proper order.

If using the provided ``make`` main function, the logging level can be set using the ``--log-level``
command line option. The default log level is ``WARN`` which means the only expected output would be
from the actions themselves.
