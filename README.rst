DynaMake - Dynamic Make in Python
=================================

.. image:: https://travis-ci.org/tanaylab/dynamake.svg?branch=master
    :target: https://travis-ci.org/tanaylab/dynamake
    :alt: Build Status

.. image:: https://readthedocs.org/projects/dynamake/badge/?version=latest
    :target: https://dynamake.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

WHY
---

    *"What the world needs is another build tool"*

    -- Way too many people

So, why yet *another* one?

DynaMake's raisons d'etre are:

* First class support for dynamic build graphs.

* Fine-grained configuration control.

* Python implementation.

DynaMake was created to address a concrete need for repeatable configurable
processing in the context of scientific computation pipelines, but should be
applicable in wider problem domains.

Dynamic Build Graphs
....................

This is a fancy way of saying that the following are supported:

**Dynamic inputs**: The full set of inputs of a build step may depend on a
subset of its inputs.

An example of dynamic inputs is compiling a C source file, which actually
depends on all the included header files. For a more complex example, consider
data analysis where the input data is classified into one of several
categories. The actual analysis results are obtained by a category-specific
algorithm, which generates different input files for the final consolidation
step.

Dynamic inputs are supported in various ways by most build tools - some of
these ways being more convoluted than others. DynaMake provides natural
first-class support for such cases.

**Dynamic outputs**: The set of outputs of a build step may depend on its
inputs.

An example of dynamic outputs is running a clustering step on some large data,
which may produce any number of clusters. Each of these clusters needs to go
through some further processing. Perhaps only some of these clusters need to be
processed (based on some expensive-to-compute filter).

Dynamic outputs are sufficiently common in scientific computation pipelines
that they are a major source of pain. There are workarounds, for sure. But
almost no existing tool has direct support for them, and of the few tools that
do, most do it as an afterthought. Since this issue has wide-ranging
implications on the build tool, this means they typically don't do it well. A
notable exception is `Shake <https://shakebuild.com/>`_, which DynaMake is
heavily inspired by.

The problem with dynamic outputs (and, to a lesser extent, dynamic inputs) is
that they make other build tool features really hard to implement. Therefore,
retrofitting them into an existing build tool causes some features to break. In
the worst case this leads to silent broken builds.

Some examples of features that become very difficult in the presence of a
dynamic build graph are:

* The ability to aggressively optimize the case when a build needs to do
  nothing at all, and in general reduce the build system overhead.

* The ability to perform a dry run that *accurately* lists *all* the steps that
  will be needed to build an arbitrary target.

* Having a purely declarative build language, which can be more easily learned
  than any programming language (even Python :-) and may be processed as pure
  data by additional tools.

Configuration Control
.....................

This is a fancy way of saying that you can tweak the parameters of arbitrary
steps of a complex pipeline, and then only execute the affected parts of the
pipeline, either all the way to the final results or just to obtain some
intermediate results to examine. This use case occurs *a lot* in scientific
computation pipelines.

Configuration parameters can be either specified as explicit command line
options for executed actions, or inside configuration files(s). These
parameters can then be used to modify the triggered command line actions. If
these differ, then DynaMake will re-invoke such actions, even if the output
files are up-to-date.

This functionality requires keeping additional persistent state between
invocation. This state is stored as human-readable (YAML) files in a special
directory (by default, ``.dynamake``, but you can override it using the
``DYNAMAKE_PERSISTENT_DIR`` environment variable). The file names are legible
(based on the step name and its parameters, if any), so it is easy to examine
them after the fact to understand exactly which parameter values were used
where.

There are good reasons to avoid any such additional persistent state. DynaMake
allows disabling this feature, switching to relying only on the modification
times of the input files. This of course results in less reliable rebuilds, so
by default this feature is enabled.

Python
......

DynaMake was initially created to address the needs of automating scientific
computation pipelines (specifically in bio-informatics, specifically in
single-cell RNA sequencing, not that it matters). However, it is a
general-purpose build tool, which may be useful for a wide range of users.

DynaMake is heavily inspired by `Shake <https://shakebuild.com/>`_. However,
``shake`` is implemented in `Haskell <https://www.haskell.org/>`_. Haskell is
unlikely to be pre-installed on a typical machine, and installing it (and
``shake``) is far from trivial, especially when one has no ``sudo`` privileges.
Also, writing ``shake`` rules uses Haskell syntax which, while being simple and
at times superior, is pretty different from that of most popular programming
languages.

In contrast, Python is much more likely to already be installed on a typical
machine. It is trivial to just type ``pip install --user dynamake`` (or ``sudo
pip install dynamake`` if you have ``sudo`` privileges). The build rules are
simple Python scripts, which means most people are already familiar with the
language, or are in the process of becoming so for other reasons.

Using a proven and familiar language is also preferable to coming up with a
whole new build-oriented language, especially when creating a general-purpose
build tool. The GNU ``make`` syntax is a warning for how such specialized
languages inevitably devolve into a general-purpose mess.

WHY NOT
-------

DynaMake's unique blend of features comes at some costs:

* It is a new, immature tool. As such, it lacks some features it could/should
  provide, is less efficient than it could be, and you may encounter the
  occasional bug. Hopefully this will improve with time. If you want
  DynaMake-like features with a proven track record, you should consider
  ``shake``.

* The provided goals, as described above, may be a poor fit for your use case.

  If your build graph and configuration are truly static, consider using `Ninja
  <https://ninja-build.org/>`_ which tries to maximize the benefits of such a
  static build pipeline. It is almost the opposite of DynaMake in this
  respect.

  If your build graph is only "mostly static" (e.g., just needs a restricted
  form of dynamic inputs, such as included header files), then you have (too)
  many other options to list here. Using the classical ``make`` is a good
  default choice.

* It is a low-level build tool, on par with ``make`` and ``ninja``.

  If you are looking for a tool that comes with a lot of built-in rules for
  dealing with specific computer languages (say, C/C++), and will automatically
  deal with cross-platform issues, consider using `CMake <https://cmake.org/>`_
  or `XMake <https://xmake.io/>`_ instead.

WHAT
----

DynaMake is essentially a Python library. There is a ``dynamake`` universal
executable script provided with the package, similar to `SCons
<https://scons.org/>`_, (which you can also invoke as ``python -m dynamake`` if
you prefer). However, you still need to write your build script in Python,
using the library's utilities, and you can also easily invoke the provided
``make`` function from your own custom main function.

DynaMake build steps may invoke applications written in any language, either
directly or by invoking shell commands, similarly to any other build tool.

Build Scripts
.............

A typical build script consists of a set of step functions, which are functions
decorated with :py:func:`dynamake.step`. This requires an explicit
``output=...`` parameter listing the file(s) created by the step.

Here is a DynaMake build script which copies the file ``foo`` to the file
``bar``, if ``bar`` does not exist, or if ``foo`` is newer than ``bar``:

.. code-block:: python

    import dynamake as dm

    @dm.step(output='foo')
    async def copy_bar_to_foo() -> None:
        dm.require('bar')
        await dm.shell('cp bar foo')

This is essentially equivalent to the ``make`` rule:

.. code-block:: make

    foo: bar
            cp bar foo

That is, DynaMake will only execute the shell command ``cp bar foo`` if the
``foo`` file is missing or is older than the ``bar`` file. In general, DynaMake
will skip actions unless it finds a sufficient reason to execute them. If there
are multiple actions in a step, and DynaMake skipped some to discover that a
later action needs to be executed, then DynaMake restarts the step, and this
time executes all actions. That is, step functions should be "idempotent";
re-running a step multiple times should in principle have no effect (other than
to modify the creation or last modification time of the output files).

The Python version is more verbose, so if this was all there was to it,
``make`` would have been preferable. However, DynaMake allows one to specify
scripts that are impossible in ``make``, justifying the additional syntax.

For example, inside each step, you can do the following:

* Invoke :py:func:`dynamake.require` to ensure the specified path exists and is
  and up-to-date. Building of required input files is done asynchronously
  (concurrently).

* Invoke ``await`` of :py:func:`dynamake.sync` to ensure all required input
  files specified so far have completed to build.

* Invoke ``await`` of :py:func:`dynamake.shell` or :py:func:`dynamake.spawn` to
  trigger the execution of a shell command or an arbitrary external program.
  This will automatically ``sync`` first to ensure all required input files
  have completed to build.

.. note::

   **Inside a step, do not simply ``await`` co-routines that are not provided
   by DynaMake.**

   DynaMake tracks the current step, and invoking ``await`` of some other
   co-routines will confuse it. Use :py:func:`dynamake.done` to ``await`` on
   external co-routines. That is, write ``await done(something())`` rather than
   ``await something()``.

* Use Python code to examine the file system, analyze the content of required
  input files (following a ``sync``), perform control flow operations
  (branches, loops), invoke Python functions which do any of these things, etc.
  It is recommended to use :py:class:`dynamake.stat.Stat` for ``stat``
  operations, as these are efficiently cached by DynaMake which results in
  faster builds.

.. note::

    **The correctness of the ``stat`` cache depends on accurate listing of each
    action's inputs and outputs.**

    In general DynaMake needs these lists to be accurate for correct operation.
    This is true of almost any build tool. In theory, one could use ``strace``
    to automatically extract the true lists of inputs and outputs, but this is
    complex, fragile (breaks for programs running on cluster servers), and
    impacts the performance.

The ability to mix general Python code together with ``make`` functionality is
what gives DynaMake its additional power over static build tools like ``make``
or ``ninja``. The following examples will demonstrate some common idioms using
this power.

Pattern Steps
.............

A more generic script might be:

.. code-block:: python

    import dynamake as dm
    from c_source_files import scan_included_files  # Assume this for simplicity.

    # Naive: does not handle a cycle of files including each other, does not
    # allow for missing include files (e.g. in #ifdef), doesn't cache results,
    # etc.
    def require_included_files(paths: *Strings) -> None:
        dm.require(*paths)
        sync()
        for included_path in dm.each_string(*paths):
            require_included_files(scan_included_files(included_path))

    @dm.step(output='obj/{*name}.o')
    async def make_object(name: str) -> None:
        source_path = f'src/{name}.c'
        require_included_files(source_path)
        await dm.spawn('cc', '-o', f'obj/{name}.o', source_path)

    @dm.step(output='bin/main')
    async def make_executable() -> None:
        object_paths = dm.glob_fmt('src/{*name}.c', 'obj/{name}.o')
        dm.require(object_paths)
        await dm.spawn('ld', '-o', 'bin/main.o', object_paths)

This demonstrates some additional concepts:

* If the ``output`` of a step contains a :py:func:`dynamake.capture` pattern,
  then the extracted values are passed to the function as string arguments.
  These can be used inside the function to generate file names (in the above,
  the source file names).

  This is similar to ``make`` pattern rules, but is more powerful, as you can
  specify multiple parts of the file name to be captured. A pattern such as
  ``foo/{*phase}/{*part}/bar`` is essentially impossible to express in
  ``make``.

  When a target is :py:func:`dynamake.require`-d, it is matched against these
  patterns, and the unique step that matches the target is triggered, with the
  appropriate (extracted) arguments. If multiple such patterns match the file,
  the one with the highest step ``priority`` is used. It is an error for more
  than one step with the same priority to match the same output file. If no
  step matches, the target is assumed to be a source file, and must exist on
  the disk. Otherwise, DynaMake complains it doesn't know how to make this
  target.

* DynaMake provides many functions to deal with ``glob``-ing, capturing, and
  formatting lists of strings. These make it convenient to perform common
  operations. For example, :py:func:`dynamake.expand` is equivalent to
  :py:func:`dynamake.fmt` using the ``kwargs`` of the current step.
  Another example is :py:func:`dynamake.glob_fmt` which uses a ``glob`` to
  obtain a list of file names, then ``extract`` some part(s) of each, then
  ``fmt`` some other pattern(s) using these values.

* Most DynaMake functions accept :py:class:`Strings`, that is, either a single
  string, or a list of strings, or a list of list of strings, etc.; and return
  either a single string or a flat list of strings. This makes it easy to
  combine the results of several functions to another function. You can also
  use this in your own functions, for example in ``require_included_files``.

* The ``output`` of a step is also ``Strings``, that is, the file or list of
  files that are created by the actions in the step. In contrast, many tools
  (most notably, ``make``) can't handle the notion of multiple outputs from a
  single step.

* The ``require_included_files`` is an example of how a step can examine the
  content of some required input file(s) to determine whether it needs
  additional required input file(s), or, in general, to make any decisions on
  how to proceed further. Note that it tries to ``require`` as many files as
  possible concurrently before invoking ``sync``. Actual processing
  (``scan_included_files``) is done serially.

Dynamic Outputs
...............

When a step may produce a dynamic set of outputs, it must specify an ``output``
pattern which includes some non captured parts (whose name starts with ``_``).
For example:

.. code-block:: python

    import dynamake as dm

    @dm.step(output=['unzipped_messages/{*id}/{*_part}.txt',
                     'unzipped_messages/{*id}/.all.done')
    async def unzip_message(id: str) -> None:
        dm.require(f'zipped_messages/{id}.zip')
        await dm.shell('unzip ...')
        await dm.shell(f'touch unzipped_messages/{id}/.all.done')

Note that only ``id`` will be set in ``kwargs``. DynaMake assumes that the same
single invocation will generate all ``_part`` values. This demonstrates another
point: if a step specifies multiple ``output`` patterns, each must capture the
same named argument(s) (in this case ``name``), but may include different (or
no) non-captured path parts.

Requiring *any* of the specific output files will cause the step to be invoked
and ensure *all* outputs are up-to-date. A common trick, demonstrated above, it
to have an additional final file serve as a convenient way to require all the
files. This allows to query the filesystem for the full list of files. For
example, assume each part needs to be processed:

.. code-block:: python

    @dm.step(output='processed_messages/{*id}/{*part}.txt')
    async def process_part(id: str, part: str) -> None:
        dm.require(f'unzipped_messages/{id}/{part}.txt')
        ...

And that all parts need to be collected together:

.. code-block:: python

    @dm.step(output='collected_messages/{*id}.txt')
    async def collect_parts(id: str) -> None:
        dm.require(f'unzipped_messages/{id}/.all.done')
        await dm.sync()
        all_parts = dm.glob_fmt(f'unzipped_messages/{id}' + '/{*part}.txt',
                                f'processed_messages/{id}' + '/{part}.txt')
        await dm.shell('cat', sorted(all_parts), '>', f'collected_messages/{id}.txt')

This sort of flow can only be approximated using static build tools. Typically
this is done using explicit build phases, instead of a unified build script.
This results in brittle build systems, where the safe best practice if anything
changes is to "delete all files and rebuild" to ensure the results are correct.

Universal Main Program
......................

The easiest way to invoke DynaMake is to place your steps inside
``DynaMake.py`` (or modules included by ``DynaMake.py``) and invoke the
provided ``dynamake`` script (which is equivalent to running ``python -m
dynamake``).

You can specify explicit ``--module`` options in the command line to directly
import your step functions from arbitrary Python modules, instead of the
default ``DynaMake.py`` file.

You can also write your own executable script:

.. code-block:: python

    import argparse
    import dynamake as dm
    import my_steps

    dm.make(argparse.ArgumentParser(...))

Which will come pre-loaded with your own steps, and allow you to tweak the
program's help message and other aspects, if needed. This is especially useful
if you are writing a package that wants to provide pre-canned steps for
performing some complex operation (such as a scientific computation pipeline).

Finally, you can directly invoke the lower-level API to use build steps as part
of your code. See the implementation of the ``make`` function and the API
documentation for details.

Annotations
...........

DynaMake allows attaching annotations
(:py:class:`dynamake.AnnotatedStr`) to strings (and patterns). Multiple
annotations may be applied to the same string. The provided string processing
functions preserve these (that is, pass the annotations from the input(s) to
the output(s)). These annotations are used by DynaMake to modify the handling
of required and output files, and in some cases, control formatting.

* :py:func:`dynamake.optional` indicates that an output need not exist at the
  end of the step, or a required file need not exist for the following actions
  to succeed. That is, invoking ``require(optional('foo'))`` will invoke the
  step that provides ``foo``. If there is no such step, then ``foo`` need not
  exist on the disk. If this step exists, and succeeds, but does not in fact
  create ``foo``, and specifies ``output=optional('foo')``, then DynaMake will
  accept this and continue. If either of the requiring or invoked steps did not
  specify the ``optional`` annotation, then DynaMake will complain and abort
  the build.

* :py:func:`dynamake.exists` ignores the modification time of an input or an
  output, instead just considering whether it exists. That is, invoking
  ``require(exists('foo'))`` will attempt to build ``foo`` but will ignore its
  timestamp when deciding whether to skip the execution of following actions in
  this step. Specifying ``output=exists('foo')`` will disable touching the
  output file to ensure it is newer than the required input file(s) (regardless
  of the setting of ``--touch_success_outputs``).

* :py:func:`dynamake.precious` prevents output file(s) from being removed
  (regardless of the setting of ``--remove_stale_outputs`` and
  ``--remove_failed_outputs``).

* :py:func:`dynamake.phony` marks an output as a non-file target. Typically
  the default top-level ``all`` target is ``phony``, as well as similar
  top-level targets such as ``clean``. When a step has any ``phony`` output(s),
  its actions are always executed, and a synthetic modification time is
  assigned to it: one nanosecond newer than the newest required input.

  If using persistent state to track actions (see below), this state will
  ignore any parts of invoked commands that are marked as ``phony``. This
  prevents changes to irrelevant command line options from triggering a
  rebuild. For example, changing the value passed to the ``--jobs`` command
  line option of a program should not impact its outputs, and therefore should
  not trigger a rebuild.

* :py:func:`dynamake.emphasized` is used by ``shell`` and ``spawn``. Arguments
  so annotated are printed in **bold** in the log file. This makes it easier
  to see the important bits of long command lines.

Control Flags
.............

The behavior of DynaMake can be tweaked by modifying the built-in global
parameter values. This is typically done by specifying the appropriate command
line option, which is then handled by the provided ``make`` main function.

* ``--rebuild_changed_actions`` controls whether DynaMake uses the persistent
  state to track the list of outputs, inputs, invoked sub-steps, and actions
  with their command line options. This ensures that builds are repeatable
  (barring changes to the environment, such as compiler versions etc.). By
  default this is ``True``.

  Persistent state is kept in YAML files named
  ``.dynamake/step_name.actions.yaml`` or, for parameterized steps,
  ``.dynamake/step_name/param=value&...&param=value.actions.yaml``. As a
  convenience, this state also includes the start and end time of each of the
  invoked actions. This allows post-processing tools to analyze the behavior of
  the build script (as an alternative to analyzing the log messages).

* ``--failure_aborts_build`` controls whether DynaMake stops the build process
  on the first failure. Otherwise, it attempts to continue to build as many
  unaffected targets as possible. By default this is ``True``.

* ``--remove_stale_outputs`` controls whether DynaMake removes all
  (non-``precious``) outputs before executing the first action of a step. By
  default this is ``True``.

* ``--wait_nfs_outputs`` controls whether DynaMake will wait before pronouncing
  that an output file has not been created by the step action(s). This may be
  needed if the action executes on a server in a cluster using an NFS shared
  file system, as NFS clients are typically caching ``stat`` results (for
  performance).

* ``--nfs_outputs_timeout`` controls the amount of time DynaMake will wait for
  output files to appear after the last step action is done. By default this is
  60 seconds, which is the default NFS stat cache timeout. However, heavily
  loaded NFS servers have been known to lag for longer of periods of time.

* ``--touch_success_outputs`` controls whether DynaMake should touch
  (non-``exists``) output file(s) to ensure their modification time is later
  than that of (non-``exists``) required input files(s). By default this is
  ``False`` because DynaMake uses the nanosecond modification time, which is
  supported on most modern file systems. The modification times on old file
  systems used a 1-second resolution, which could result in the output having
  the same modification time as the input for a fast operation.

  This option might still be needed if an output is a directory (not a file)
  and is ``precious`` or ``--remove_stale_outputs`` is ``False``. In this case,
  the modification time of a pre-existing directory will not necessarily be
  updated to reflect the fact that output file(s) in it were created or
  modified by the action(s). In general it is not advised to depend on the
  modification time of directories; it is better to specify a glob matching the
  expected files inside them, or use an explicit timestamp file.

* ``--remove_failed_outputs`` controls whether DynaMake should remove
  (non-``precious``) output files when a step action has failed. This prevents
  corrupt output file(s) from remaining on the disk and being used in later
  invocations or by other programs. By default this is ``True``.

* ``-remove_empty_directories`` controls whether DynaMake will remove empty
  directories which result from removing any output file(s). By default this is
  ``False``.

* ``--jobs`` controls the maximal number of ``shell`` or ``spawn`` actions that
  are invoked at the same time.

  A value of ``0`` will allow for unlimited number of parallel actions. This is
  useful if actions are to be be executed on a cluster of servers instead of on
  the local machine, or if some other resource(s) are used to restrict the
  number of parallel actions (see below).

  A positive value will force executing at most this number of parallal
  actions. For example, a value of ``1`` will force executing just one action
  at a time.

  A negative value will force executing a fraction of the number of logical
  processors (``nproc``) in parallel. For example, ``-1`` will execute at most
  one action per logical processor, and ``-2`` will execute at most one action
  per two logical processors, useful to force executing at most one action per
  physical core on system with two hyper-threads (logical processors) per
  physical core.

  The default value is ``-1``. You can override this default using the
  ``DYNAMAKE_JOBS`` environment variable.

.. note::

    **The DynaMake python code itself is not parallel.**

    DynaMake always runs on a single process. Parallelism is the result of
    DynaMake executing an external action, and instead of waiting for it to
    complete, switching over to a different step and processing it until it
    also executes an external action, and so on. Thus actions may execute in
    parallel, while the Python code is still doing only one thing at a time.
    This greatly simplifies reasoning about the code. Specifically, if a piece
    of code contains no ``await`` calls, then it is guaranteed to "atomically"
    execute to completion, so there is no need for a lock or a mutex to
    synchronize between the steps, even when they share some data.

Build Configuration
...................

The above control flags are an example of global build configuration
parameters. In general, such parameters have a default, can be overridden by
some command line option, and may be used by any (possibly nested) function of
the program.

You can add your own custom configuration parameters. For example:

.. code-block:: python

    import dynamake as dm

    mode = dm.Param('mode', ...)

    MODE_FLAGS = {
        'debug': [ ... ],
        'release': [ ... ],
    }

    @dm.step(output='obj/{*name}.o')
    async def make_object(name: str) -> None:
        dm.require(f'src/{name}.c')
        await dm.spawn('cc', '-o', f'obj/{name}.o', MODE_FLAGS[mode.value], source_path)

That is, constructing a new :py:class:`dynamake.application.Param` specifies
the name, default value and command line option(s) for the parameter. The
:py:func:`dynamake.application.Param.value` property is set to the effective
value of the parameter and can be used to modify some step's behavior in
arbitrary ways. This value is either the parameter's default, or the value
loaded from the default ``DynaMake.yaml`` configuration file, or the value
loaded from another configurtion file by using the ``--config``, or the value
specified in an explicit command line option for the parameter, in ascending
priority order.

Parallel Resources
..................

As mentioned above, DynaMake will perform all ``require`` operations
concurrently, up to the next ``sync`` call of the step (which automatically
happens before any ``shell`` or ``spawn`` action). As a result, by default
DynaMake will execute several actions in parallel, subject to the setting of
``--jobs``.

It is possible to define some additional resources using
:py:func:`dynamake.resources` to restrict parallel execution. For example,
invoking ``resource_parameters(ram=1, io=1)`` will create two new resources,
``ram`` and ``io``, which must have been previously defined using configuration
``Param`` calls. The values specified are the default consumption for actions
that do not specify an explicit value.

Then, when invoking ``shell`` or ``spawn``, it is possible to add ``ram=...``
and/or ``io=...`` named arguments to the call, to override the expected
resource consumption of the action. DynaMake will ensure that the sum of these
expected consumptions will never exceed the established limit.

Logging
.......

Complex build scripts are notoriously difficult to debug. To help alleviate
this pain, DynaMake uses the standard Python logging mechanism, and supports
the following logging levels:

* ``INFO`` prints only the executed actions. This is similar to the default
  ``make`` behavior. Use this if you just want to know what is being run, when
  all is well. If ``--log_skipped_actions`` is set, then this will also log
  skipped actions.

* ``FILE`` also print file operations done by DynaMake itself, specifically
  touching and removing files (controlled by the flags
  ``--touch_success_outputs``, ``--remove_stale_outputs`` and
  ``--remove_failed_outputs``). This gives a more complete picture of the
  effect DynaMake had on the file system.

* ``WHY`` also prints the reason for executing each action (which output file
  does not exist and needs to be created, which input file is newer than which
  output file, etc.). This is useful for debugging the logic of the build
  script.

* ``TRACE`` also prints each step invocation. This can further help in
  debugging the logic of the build script.

* ``DEBUG`` prints a lot of very detailed information about the flow. Expanded
  globs, the full list of input and output files, the configuration files used,
  etc. This is useful in the hopefully very rare cases when the terse output
  from the ``WHY`` and ``TRACE`` levels is not sufficient for figuring out what
  went wrong.

The ``FILE``, ``WHY`` and ``TRACE`` levels are not a standard python log level.
They are defined to be between ``INFO`` and ``DEBUG``, in the proper order.

If using the provided ``make`` main function, the logging level can be set
using the ``--log-level`` command line option. The default log level is
``WARN`` which means the only expected output would be from the actions
themselves.

WHAT NOT (YET)
--------------

Since DynaMake is very new, there are many features that should be implemented,
but haven't been worked on yet:

* Improve the documentation. This README covers the basics but there are
  additional features that are only mentioned in the class and function
  documentation, and deserves a better description.

* Allow forcing rebuilding (some) targets.

* Dry run. While it is impossible in general to print an accurate full set of
  dry run actions, if should be easy to just print the 1st action(s) that need
  to be executed. This should provide most of the value. It should also be
  possible to provide a longer list of actions assuming that any steps with
  dynamic outputs generate only the same set of outputs as before (persistent
  data is available) or just a single output with a synthetic name (otherwise),
  which might provide addititional value.

* Allow automated clean actions based on the collected step outputs. If there's
  nothing to be done when building some target(s), then all generated output
  files (with or without the ultimate targets) should be fair game to being
  removed as part of a clean action. However, due to the dry-run problem, we
  can't automatically clean outputs of actions that depend on actions that
  still need to be executed.

* Allow skipping generating intermediate files if otherwise no actions need to
  be done. This is very hard to do with a dynamic build graph - probably
  impossible in the general case, but common cases might be possible(?)

* Generate a tree (actually a DAG) of step invocations. This can be collected
  from the persistent state files.

* Generate a visualization of the timeline of action executions showing start
  and end times, with resource consumption.

* Allow using checksums instead of timestamps to determine if actions can be
  skipped, either by default or on a per-file basis.
