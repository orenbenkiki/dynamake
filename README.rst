DynaMake - Dynamic Make in Python
=================================

WHY
---

    *"What the world needs is another build tool"*

    -- Way too many people

So, why yet *another* one?

DynaMake's raisons d'etre are:

* First class support for dynamic build graphs.

* Fine-grained configuration control.

* Python implementation.

DynaMake was created to address a concrete need for repeatable configurable processing in the
context of scientific computation pipelines, but should be applicable in wider problem domains.

Dynamic Build Graphs
....................

This is a fancy way of saying that the following are supported:

**Dynamic inputs**: The full set of inputs of a build step may depend on a subset of its inputs.

An example of dynamic inputs is compiling a C source file, which actually depends on all the
included header files. For a more complex example, consider data analysis where the input data is
classified into one of several categories. The actual analysis results are obtained by a
category-specific algorithm, which generates different input files for the final consolidation step.

Dynamic inputs are supported in various ways by most build tools - some of these ways being more
convoluted than others. DynaMake provides natural first-class support for such cases.

**Dynamic outputs**: The set of outputs of a build step may depend on its inputs.

An example of dynamic outputs is running a clustering step on some large data, which may produce any
number of clusters. Each of these clusters needs to go through some further processing. Perhaps only
some of these clusters need to be processed (based on some expensive-to-compute filter).

Dynamic outputs are sufficiently common in scientific computation pipelines that they are a major
source of pain. There are workarounds, for sure. But almost no existing tool has direct support for
them, and of the few tools that do, most do it as an afterthought. Since this issue has wide-ranging
implications on the build tool, this means they typically don't do it well. A notable exception is
`Shake <https://shakebuild.com/>`_, which DynaMake is heavily inspired by.

The problem with dynamic outputs (and, to a lesser extent, dynamic inputs) is that they make other
build tool features really hard to implement. Therefore, retrofitting them into an existing build
tool causes some features to break. In the worst case this leads to silent broken builds.

Some examples of features that become very difficult in the presence of a dynamic build graph are:

* The ability to aggressively optimize the case when a build needs to do nothing at all, and
  in general reduce the build system overhead.

* The ability to perform a dry run that accurately lists *all* the steps that will be needed to
  build an arbitrary target.

* Having a purely declarative build language, which can be more easily learned than any programming
  language (even Python :-) and may be processed as pure data by additional tools.

Configuration Control
.....................

This is a fancy way of saying that you can tweak the parameters of arbitrary steps of a complex
pipeline, and then only execute the affected parts of the pipeline, either all the way to the final
results or just to obtain some intermediate results to examine. This use case occurs *a lot* in
scientific computation pipelines.

Configuration parameters can be either specified as explicit command line options for executed
actions, or inside configuration files(s). A few build tools will trigger a rebuild if the command
line options have changed. All build tools will allow adding a configuration file as a dependency;
however, this requires setting up a per-invocation configuration file which can become very unwieldy
for a large pipeline - especially when the "same" pattern step needs to be invoked with different
configurations depending on the exact files (e.g., different compilation flags for different source
files).

DynaMake automates the generation of a per-invocation configuration file, based on a single central
configuration file, so that on modification, only the affected actions are invoked. In addition,
DynaMake tracks the list of inputs, outputs, actions and their command line options, so that any
change in the pipeline itself will trigger the invocation of the affected actions.

This functionality requires keeping additional persistent state between invocation. This state is
stored as human-readable (YAML) files in a special directory (by default, ``.dynamake``, but you can
override it using the ``DYNAMAKE_PERSISTENT_DIR`` environment variable). The file names are legible
(based on the step name and its parameters, if any), so it is easy to examine them after the fact to
understand exactly which parameter values were used where.

In rare cases, there are good reasons to avoid any such additional persistent state. DynaMake allows
disabling these features, switching to relying only on the modification times of the input files.
This of course results in less reliable rebuilds.

Python
......

DynaMake was initially created to address the needs of automating scientific computation pipelines
(specifically in bio-informatics, specifically in single-cell RNA sequencing, not that it matters).
However, it is a general-purpose build tool, which may be useful for a wide range of users.

DynaMake is heavily inspired by `Shake <https://shakebuild.com/>`_. However, ``shake`` is
implemented in `Haskell <https://www.haskell.org/>`_. Haskell is unlikely to be pre-installed on a
typical machine, and installing it (and ``shake``) is far from trivial, especially when one has no
``sudo`` privileges. Also, writing ``shake`` rules uses Haskell syntax which, while being simple and
at times superior, is pretty different from that of most popular programming languages.

In contrast, Python is much more likely to already be installed on a typical machine. It is trivial
to just type ``pip install --user dynamake`` (or ``sudo pip install dynamake`` if you have ``sudo``
privileges). The build rules are simple Python scripts, which means most people are already familiar
with the language, or are in the process of becoming so for other reasons.

Using a proven and familiar language is also preferable to coming up with a whole new build-oriented
language, especially when creating a general-purpose build tool. The GNU ``make`` syntax is a
warning for how such specialized languages inevitably devolve into a general-purpose mess.

WHY NOT
-------

DynaMake's unique blend of features comes at some costs:

* It is a new, immature tool. As such, it lacks some features it could/should provide,
  is less efficient than it could be, and you may encounter the occasional bug. Hopefully this will
  improve with time. If you want DynaMake-like features with a proven track record, you should
  consider ``shake``.

* The provided goals, as described above, may be a poor fit for your use case.

  If your build graph and configuration are truly static, consider using `Ninja
  <https://ninja-build.org/>`_ which tries to maximize the benefits of such a static build pipeline.
  It is almost the opposite of DynaMake in this respect.

  If your build graph is only "mostly static" (e.g., just needs a restricted form of dynamic inputs,
  such as included header files), then you have (too) many other options to list here. Using the
  classical ``make`` is a good default choice.

* It is a low-level build tool, on par with ``make`` and ``ninja``.

  If you are looking for a tool that comes with a lot of built-in rules for dealing with specific
  computer languages (say, C/C++), and will automatically deal with cross-platform issues,
  consider using `CMake <https://cmake.org/>`_ or `XMake <https://xmake.io/>`_ instead.

WHAT
----

DynaMake is essentially a Python library. There is a ``dynamake`` universal executable script
provided with the package, similar to `SCons <https://scons.org/>`_, but you still need to write
your build script in Python, using the library's utilities, and you can also easily invoke the
provided ``make`` main function from your code. You can even directly invoke the build functionality
from your own custom main function.

DynaMake build steps may invoke applications written in any language, which are configured in any
way (command line flags, configuration files, etc.).

As a convenience, DynaMake also provides utilities for writing Python "configurable applications"
which make heavy use of DynaMake's automated configuration control. A ``dynamain`` universal
executable script removes the need to wrap each Python function in its own executable script, and
you can easily invoke the provided ``main`` function from your code. You can even directly invoke
the configurable application functions from your own custom main function.

Build Scripts
.............

A typical build script consists of a set of step functions, which are functions decorated with
:py:func:`dynamake.make.step`. This requires an explicit ``output=...`` parameter listing the
file(s) created by the step.

Here is a DynaMake build script which copies the file ``foo`` to the file ``bar``, if ``bar`` does
not exist, or if ``foo`` is newer than ``bar``:

.. code-block:: python

    import dynamake.make as dm

    @dm.step(output='foo')
    async def copy_bar_to_foo() -> None:
        dm.require('bar')
        await dm.shell('cp bar foo')

This is essentially equivalent to the ``make`` rule:

.. code-block:: make

    foo: bar
            cp bar foo

That is, DynaMake will only execute the shell command ``cp bar foo`` if the ``foo`` file is missing
or is older than the ``bar`` file. In general, DynaMake will skip actions unless it finds a
sufficient reason to execute them. If there are multiple actions in a step, and DynaMake skipped
some to discover that a later action needs to be executed, then DynaMake restarts the step, and this
time executes all actions. That is, step functions are (should be) "idempotent"; re-running a step
multiple times should have no effect.

The Python version is more verbose, so if this was all there was to it, ``make`` would have been
preferable. However, DynaMake allows one to specify scripts that are impossible in ``make``,
justifying the additional syntax.

For example, inside each step, you can do the following:

* Invoke :py:func:`dynamake.make.require` to ensure the specified path exists and is and up-to-date.
  Building of required input files is done asynchronously (concurrently).

* Invoke ``await`` of :py:func:`dynamake.make.sync` to ensure all required input files specified so
  far have completed to build.

* Invoke ``await`` of :py:func:`dynamake.make.shell` or :py:func:`dynamake.make.spawn` to trigger
  the execution of a shell command or an external program. This will automatically ``sync`` first
  to ensure all required input files have completed to build.

.. note::

   **Inside a step, do not simply ``await`` co-routines that are not provided by DynaMake.**

   DynaMake tracks the current step, and invoking ``await`` of some other co-routines will confuse
   it. Use :py:func:`dynamake.make.done` to ``await`` on external co-routines. That is, write
   ``await done(something())`` rather than ``await something()``.

* Use Python code to examine the file system (it is recommended to use
  :py:class:`dynamake.stat.Stat` for cached ``stat`` operations), analyze the content of
  required input files (following a ``sync``), perform control flow operations (branches, loops),
  invoke Python functions which do any of these things, etc.

.. note::

    **The correctness of the ``stat`` cache depends on accurate listing of each action's inputs and
    outputs.**

    In general DynaMake needs these lists to be accurate for correct operation. This is true of
    almost any build tool. In theory, one could use ``strace`` to automatically extract the true
    lists of inputs and outputs, but this is complex, fragile (breaks for programs running on
    cluster servers), and impacts the performance.

The ability to mix general Python code together with ``make`` functionality is what gives DynaMake
its additional power over static build tools like ``make`` or ``ninja``. The following examples
will demonstrate some common idioms using this power.

Pattern Steps
.............

A more generic script might be:

.. code-block:: python

    import dynamake.make as dm
    from c_source_files import scan_included_files  # Assume this for simplicity.

    # Naive: does not handle a cycle of files including each other,
    # does not allow for missing include files (e.g. in #ifdef),
    # doesn't cache results, etc.
    def require_included_files(paths: *Strings) -> None:
        dm.require(*paths)
        sync()
        for included_path in dm.each_string(*paths):
            require_included_files(scan_included_files(included_path))

    @dm.step(output='obj/{*name}.o')
    async def make_object(**kwargs: str) -> None:
        source_path = 'src/{name}.c'.format(**kwargs)
        source_path = dm.fmt(kwargs, 'src/{name}.c')  # Same as above
        source_path = dm.e('src/{name}.c')  # Same as above
        require_included_files(source_path)
        await dm.espawn('cc', '-o', 'obj/{name}.o', source_path)

    @dm.step(output='bin/main')
    async def make_executable() -> None:
        object_paths = dm.glob_fmt('src/{*name}.c', 'obj/{name}.o')
        dm.require(object_paths)
        await dm.spawn('ld', '-o', 'bin/main.o', object_paths)

This demonstrates some additional concepts:

* If the ``output`` of a step contains a :py:func:`dynamake.patterns.capture` pattern, then the
  extracted values are passed to the function as string arguments. These can be used inside the
  function to generate file names (in the above, the source file names).

  This is similar to ``make`` pattern rules, but is more powerful, as you can specify multiple parts
  of the file name to be captured. A pattern such as ``foo/{*phase}/{*part}/bar`` is essentially
  impossible to express in ``make``.

  When a target is ``require``-d, it is matched against these patterns, and the unique step that
  matches the target is triggered, with the appropriate (extracted) arguments. It is an error for
  more than one step to match. If no step matches, the target is assumed to be a source file, and
  must exist on the disk. Otherwise, DynaMake complains it doesn't know how to make this target.

* DynaMake provides many functions to deal with ``glob``-ing, capturing, and formatting lists
  of strings, listed in the :py:func:`dynamake.patterns` module. These make it convenient to perform
  common operations. For example, ``:py:func:`dynamake.make.e`` is equivalent to
  :py:func:`dynamake.patterns.fmt` using the ``kwargs`` of the current step. This is an extremely
  common operation so we give it such a short function name. Another example is
  :py:func:`dynamake.patterns.glob_fmt` which uses a ``glob`` to obtain a list of file names, then
  ``extract`` some part(s) of each, then ``fmt`` some other pattern(s) using these values.

* Most DynaMake functions accept :py:class:`Strings`, that is, either a single string, or a list of
  strings, or a list of list of strings, etc.; but they return either a single string or a flat list
  of strings. This makes it easy to pass the output of one function to another. You can also use
  this in your own functions, for example in ``require_included_files``.

* The ``output`` of a step is also ``Strings``, that is, may be a list of files that are created
  by the actions in the step. In contrast, many tools (most notably, ``make``) can't handle the
  notion of multiple outputs from a single step.

* The ``require_included_files`` is an example of how a step can examine the content of some
  required input file(s) to determine whether it needs additional required input file(s), or, in
  general, to make any decisions on how to proceed further. Note that it tries to ``require`` as
  many files as possible concurrently before invoking ``sync``. Actual processing
  (``scan_included_files``) is done serially.

Dynamic Outputs
...............

When a step may produce a dynamic set of outputs, it must specify an ``output`` pattern
which includes some non captured parts (whose name starts with ``_``). For example:

.. code-block:: python

    import dynamake.make as dm

    @dm.step(output=['unzipped_messages/{*id}/{*_part}.txt',
                     'unzipped_messages/{*id}/.all.done')
    async def unzip_message(**kwargs: str) -> None:
        dm.require('zipped_messages/{id}.zip'.format(**kwargs))
        await dm.shell('unzip ...')
        await dm.eshell('touch unzipped_messages/{id}/.all.done')

Note that only ``id`` will be set in ``kwargs``. DynaMake assumes that the same invocation will
generate all ``_part`` values in one call. This demonstrates another point: if a step specifies
multiple ``output`` patterns, each must capture the same named argument(s) (in this case ``name``),
but may include different (or no) non-captured path parts.

The :py:func:`dynamake.make.eshell` is equivalent to ``shell(e(...))``, that is, it automatically
formats all the string(s) using the step's ``kwargs``. DynaMake defines several additional such
functions with an ``e`` prefix, for example :py:func:`dynamake.make.erequire` and
:py:func:`dynamake.make.eglob_paths`.

Requiring *any* of the specific output files will cause the step to be invoked and ensure *all*
outputs are up-to-date. A common trick, demonstrated above, it to have an additional final file
serve as a convenient way to require all the files. This allows to query the filesystem for the full
list of files. For example, assume each part needs to be processed:

.. code-block:: python

    @dm.step(output='processed_messages/{*id}/{*part}.txt')
    async def process_part(**kwargs) -> None:
        dm.require('unzipped_messages/{id}/{part}.txt'.format(**kwargs))
        ...

And that all parts need to be collected together:

.. code-block:: python

    @dm.step(output='collected_messages/{*id}.txt')
    async def collect_parts(**kwargs) -> None:
        dm.require('unzipped_messages/{id}/.all.done'.format(**kwargs))
        await dm.sync()
        all_parts = dm.eglob_fmt('unzipped_messages/{id}/{*part}.txt',
                                 'processed_messages/{id}/{*part}.txt')
        await dm.eshell('cat', sorted(all_parts), '>', 'collected_messages/{id}.txt')

This sort of flow can only be approximated using static build tools. Typically this is done using
explicit build phases, instead of a unified build script. This results in brittle build systems,
where the safe best practice if anything changes is to "delete all files and rebuild" to ensure the
results are correct.

Universal Main Program
......................

Installing DynaMake provides a universal executable build script called ``dynamake``, which is a
thin wrapper around the generic :py:func:`dynamake.make.make` main function. The easiest way to
invoke DynaMake is to place your steps inside ``DynaMake.py`` (or modules included by
``DynaMake.py``) and invoke this ``dynamake`` script. You can also specify explicit ``--module``
options in the command line to directly import your step functions from other Python modules.

You can write your own executable script:

.. code-block:: python

    import argparse
    import dynamake.make as dm
    import my_steps

    dm.make(argparse.ArgumentParser(description='...'))

Which will come pre-loaded with your own steps, and allow you to tweak the program's help message
and other aspects, if needed. This is especially useful if you are writing a package that wants to
provide pre-canned steps for performing some complex operation (such as a scientific computation
pipeline).

Finally, you can directly invoke the lower-level API to use build steps as part of your code.
See the implementation of the ``make`` function and the API documentation for details.

Annotations
...........

DynaMake allows attaching annotations (:py:class:`dynamake.patterns.AnnotatedStr`) to strings (and
patterns). Multiple annotations may be applied to the same string. The provided string processing
functions preserve these (that is, pass the annotations from the input(s) to the output(s)). These
annotations are used by DynaMake to modify the handling of required and output files, and in some
cases, control formatting.

* :py:func:`dynamake.patterns.optional` indicates that an output need not exist at the end of the
  step, or a required file need not exist for the following actions to succeed. That is, invoking
  ``require(optional('foo'))`` will invoke the step that provides ``foo``. If there is no such step,
  then ``foo`` need not exist on the disk. If this step exists, and succeeds, but does not in fact
  create ``foo``, and specifies ``output=optional('foo')``, then DynaMake will accept this and
  continue. If either of the steps did not specify the ``optional`` annotation, then DynaMake will
  complain and abort the build.

* :py:func:`dynamake.patterns.exists` ignores the modification time of an input or an output,
  instead just considering whether it exists. That is, invoking ``require(exists('foo'))``
  will attempt to build ``foo`` but will ignore its timestamp when deciding whether to
  skip the execution of following actions in this step. Specifying ``output=exists('foo')``
  will disable touching the output file to ensure it is newer than the required input file(s)
  (regardless of the setting of ``--touch_success_outputs``).

* :py:func:`dynamake.patterns.precious` prevents output file(s) from being removed
  (regardless of the setting of ``--remove_stale_outputs`` and ``--remove_failed_outputs``).

* :py:func:`dynamake.patterns.phony` marks an output as a non-file target. Typically the
  default top-level ``all`` target is ``phony``, as well as similar top-level targets such as
  ``clean``. When a step has any ``phony`` output(s), its actions are always executed, and a
  synthetic modification time is assigned to it: one nanosecond newer than the newest required
  input.

  If using persistent state to track actions (see below), this state will ignore any parts of
  invoked commands that are marked as ``phony``. This prevents changes irrelevant command line
  options from triggering a rebuild. For example, changing the value passed to the ``--jobs``
  command line option of a program should not impact its outputs, and therefore should not trigger a
  rebuild.

  TODO: Clarify distinction between phony and optional (especially when using persistent state).

* :py:func:`dynamake.patterns.emphasized` is used by ``shell`` and ``spawn``. Arguments
  so annotated are printed in **bold** in the log file. This makes it easier to see the important
  bits of long command lines.

Control Flags
.............

The behavior of DynaMake can be tweaked by modifying the options specified in
:py:func:`dynamake.make.Make`. This is typically done by specifying the appropriate command line
option which is then handled by the provided ``make`` main function.

* ``--rebuild_changed_actions`` controls whether DynaMake uses the persistent state to track
  the list of outputs, inputs, invoked sub-steps, and actions with their command line options. This
  ensures that builds are repeatable (barring changes to the environment, such as compiler versions
  etc.). By default this is ``True``.

  Persistent state is kept in YAML files named ``.dynamake/step_name.actions.yaml`` or, for
  parameterized steps, ``.dynamake/step_name/param=value&...&param=value.actions.yaml``. As a
  convenience, this state also includes the start and end time of each of the invoked actions. This
  allows post-processing tools to analyze the behavior of the build script (as an alternative to
  analyzing the log messages).

* ``--failure_aborts_build`` controls whether DynaMake stops the build process on the first
  failure. Otherwise, it attempts to continue to build as many unaffected targets as possible.
  By default this is ``True``.

* ``--remove_stale_outputs`` controls whether DynaMake removes all (non-``precious``) outputs
  before executing the first action of a step. By default this is ``True``.

* ``--wait_nfs_outputs`` controls whether DynaMake will wait before pronouncing that an output
  file has not been created by the step action(s). This may be needed if the action executes on a
  server in a cluster using an NFS shared file system, as NFS clients are typically caching ``stat``
  results (for performance).

* ``--nfs_outputs_timeout`` controls the amount of time DynaMake will wait for output files
  to appear after the last step action is done. By default this is 60 seconds, which is the
  default NFS stat cache timeout. However, heavily loaded NFS servers have been known to
  lag for longer of periods of time.

* ``--touch_success_outputs`` controls whether DynaMake should touch (non-``exists``) output
  file(s) to ensure their modification time is later than that of (non-``exists``) required input
  files(s). By default this is ``False`` because DynaMake uses the nanosecond modification time,
  which is supported on most modern file systems. The modification times on old file systems used a
  1-second resolution, which could result in the output having the same modification time as the
  input for a fast operation.

  This option might still be needed if an output is a directory (not a file) and is ``precious`` or
  ``--remove_stale_outputs`` is ``False``. In this case, the modification time of a pre-existing
  directory will not necessarily be updated to reflect the fact that output file(s) in it were
  created or modified by the action(s). In general it is not advised to depend on the modification
  time of directories; it is better to specify a glob matching the expected files inside them, or
  use an explicit timestamp file.

* ``--remove_failed_outputs`` controls whether DynaMake should remove (non-``precious``) output
  files when a step action has failed. This prevents corrupt output file(s) from remaining on
  the disk and being used in later invocations or by other programs. By default this is ``True``.

* ``-remove_empty_directories`` controls whether DynaMake will remove empty directories resulting
  from removing any output file(s). By default this is ``False``.

* ``--jobs`` controls the maximal number of ``shell`` or ``spawn`` actions that are invoked at the
  same time. By default this is the number of (logical) processors in the system (``nproc``). A
  value of ``1`` will force executing one action at a time. You can override this default using the
  ``DYNAMAKE_JOBS`` environment variable.

  A value of ``0`` will allow for unlimited number of parallel actions. This is useful if actions
  are to be be executed on a cluster of servers instead of on the local machine, or if some other
  resource(s) are used to restrict the number of parallel actions (see below).

.. note::

    **The DynaMake python code itself is not parallel.**

    DynaMake always runs on a single process. Parallelism is the result of DynaMake executing an
    external action, and instead of waiting for it to complete, switching over to a different step
    and processing it until it also executes an external action, and so on. Thus actions may execute
    in parallel, while the Python code is still doing only one thing at a time. This greatly
    simplifies reasoning about the code. Specifically, if a piece of code contains no ``await``
    calls, then it is guaranteed to "atomically" execute to completion, so there is no need for a
    lock or a mutex to synchronize between the steps, even when they share some data.

Build Configuration
...................

The above control flags are an example of global build configuration parameters. In general, such
parameters have a default, can be overridden by some command line option, and may be used by any
(possibly nested) function of the program.

The use of global configuration parameters isn't unique to DynaMake scripts. Therefore, it has been
factored out and is provided on its own via the :py:mod:`dynamake.application` module, described
below.

A quick example of how such parameters can be used is:

.. code-block:: python

    import dynamake.make as dm

    dm.Param('mode', ...)

    MODE_FLAGS = {
        'debug': [ ... ],
        'release': [ ... ],
    }

    @dm.step(output='obj/{*name}.o')
    async def make_object(mode: str = dm.env(), **kwargs: str) -> None:
        dm.require('src/{name}.c'.format(**kwargs))
        await dm.espawn('cc', '-o', 'obj/{name}.o', MODE_FLAGS[mode], source_path)

That is, constructing a new :py:class:`dynamake.application.Param` specifies the default value and
command line option(s) for the parameter, and using :py:func:`dynamake.application.env` as the
parameter's default value will ensure the proper value is passed to the step invocation.

The provided ``make`` main function will also load the parameter values specified in the file
``DynaConf.yaml``, if it exists, or any files specified using the ``--config`` command line option.

Parallel Resources
..................

As mentioned above, DynaMake will perform all ``require`` operations concurrently, up to the next
``sync`` call of the step (which automatically happens before any ``shell`` or ``spawn`` action). As
a result, by default DynaMake will execute several actions in parallel, subject to the setting of
``--jobs``.

It is possible to define some additional resources using :py:func:`dynamake.make.resources` to
restrict parallel execution. For example, specifying ``resource_parameters(ram=1, io=1)`` will
create two new resources, ``ram`` and ``io``, which must have been previously defined using
configuration ``Param`` calls. The values specified are the default consumption for actions that do
not specify an explicit value.

Then, when invoking ``shell`` or ``spawn``, it is possible to add ``ram=...`` and/or ``io=...``
named arguments to the call, to override the expected resource consumption of the action. DynaMake
will ensure that the sum of these expected consumptions will never exceed the established limit.

Action Configuration
....................

A major use case of DynaMake is fine-grained control over configuration parameters
for controlling step actions.

For example, let's allow configuring the compilation flags in the above example(s):

.. code-block:: python

    import dynamake.make as dm

    @dm.step(output='obj/{*name}.o')
    async def make_object(**kwargs: str) -> None:
        dm.require('src/{name}.c'.format(**kwargs))
        await dm.espawn('cc', '-o', 'obj/{name}.o', dm.config_param('flags'), source_path)

And create a YAML configuration file as follows:

.. code-block:: yaml

   - when:
       step: make_object
     then:
       flags: [-g, -O2]

   - when:
       step: make_object
       name: main
     then:
       flags: [-g, -O3]

This configuration file needs to be loaded using :py:func:`dynamake.config.Config.load`. The
provided ``make`` main function will automatically load the ``DynaMake.yaml`` configuration file, if
it exists, followed by any file specified using the ``--step_config`` command line option(s), if
any.

.. note::

    **Do not confuse build and step configuration files.**

    The ``DynaConf.yaml`` and ``--config`` files control the **build** configuration parameters.
    The ``DynaMake.yaml`` and ``--step_config`` control control the **steps** configuration
    parameters. Thus the ``DynaConf.yaml`` contains simple build parameter values, while
    ``DynaMake.yaml`` contains configuration **rules** used to decide on the parameter values for
    each build step invocation.

Explicitly using configuration parameters as shown above is needed when executing generic programs.
If, however, the action invokes a program implemented using the :py:mod:`dynamake.application`
functions, it is possible to do better, by using a generated action configuration file. For
example:

.. code-block:: python

    @dm.step(output='foo')
    async def make_foo() -> None:
        require('bar')
        await spawn('dynamain', 'bar_to_foo', '--config', dm.config_file(), ...)

If :py:func:`dynamake.make.config_file` is invoked, then DynaMake will generate a configuration file
containing just the parameter values for this specific step invocation. If this file is missing or
contains different values, than it will trigger the actions, even if the output files otherwise seem
up-to-date. Thus, even if the main ``DynaConf.yaml`` file is modified, an action will only be
rebuilt if its own effective parameter values have changed.

The paths to the generated configuration files are similar to the path to the persistent state
files: ``.dynamake/step_name.config.yaml`` or
``.dynamake/step_name/param=value&param=value.config.yaml``. Thus, if for some reason you want to
avoid all persistent state, you should not use this functionality.

As an additional convenience, DynaMake provides the :py:func:`dynamake.make.submit` function which
allows the action configuration file to specify a ``run_prefix`` and/or ``run_suffix`` around what
would otherwise be a normal `spawn` action. For example:

.. code-block:: python

    @dm.step(output='foo')
    async def make_foo() -> None:
        require('bar')
        await spawn('compute_foo_from_bar', ...)

Combined with the YAML configuration file:

.. code-block:: yaml

    - when:
        step: make_foo
      then:
        run_prefix: run_on_compute_cluster

Will result in DynaMake executing the ``shell`` command ``run_on_compute_cluster
compute_foo_from_bar ...``. Both the prefix and suffix are marked as ``phony`` so modifying them
will not trigger a rebuild. By default both prefix and suffix are empty, in which case ``submit``
behaves identically to ``spawn``.

Logging
.......

Complex build scripts are notoriously difficult to debug. To help alleviate this pain, DynaMake
uses the standard Python logging mechanism, and supports the following logging levels:

* ``INFO`` prints only the executed actions. This is similar to the default ``make`` behavior.
  Use this if you just want to know what is being run, when all is well. If
  ``--log_skipped_actions`` is set, then this will also log skipped actions.

* ``WHY`` also prints the reason for executing each action (which output file does not exist and
  needs to be created, which input file is newer than which output file, etc.). This is useful
  for debugging the logic of the build script.

* ``TRACE`` also prints each step invocation. This can further help in debugging the logic of the
  build script.

* ``DEBUG`` prints a lot of very detailed information about the flow. Expanded globs, the full
  list of input and output files, the configuration files used, etc. This is useful in the hopefully
  very rare cases when the terse output from the ``WHY`` and ``TRACE`` levels is not sufficient for
  figuring out what went wrong.

The ``WHY`` and ``TRACE`` levels are not a standard python log level. They are defined to be between
``DEBUG`` and ``INFO``, in the proper order.

If using the provided ``make`` main function, the logging level can be set using the ``--log-level``
command line option. The default log level is ``WARN`` which means the only expected output would
be from the actions themselves.

Configurable Applications
.........................

A major use case for DynaMake is automating scientific computation pipelines. Such pipelines involve
multiple actions, which are often also implemented in Python. Each such action also has its own
configuration parameters, which we'd like to control using action configuration as described
above.

A realistic system has multiple such functions that need to be invoked. It is a hassle to have to
create a separate script for invoking each such function. A way around this is to create a single
script which takes the function name as a command-line argument.

DynaMake therefore factors out support for configurable Python-based programs, allowing users to
implement their own. The ``dynamake`` script itself can be seen as just another such customized
program.

DynaMake installs a universal ``dynamain`` script which is a thin wrapper for a provided
:py:func:`dynamake.application.main` function. This script automatically imports ``DynaMain.py`` if
it exists, and any other modules specified by the ``--module`` command line option.

Similarly to the ``make`` main function, you can implement your own custom script:

.. code-block:: python

    import argparse
    import dynamake.application as da
    import my_functions

    da.main(argparse.ArgumentParser(description='...'))

You can also directly invoke the lower-level API to directly invoke the functions.
See the implementation of the ``main`` function and the API documentation for details.

Here is a trivial example configurable function which can be invoked from the command line using
this mechanisms:

.. code-block:: python

    import dynamake.application as da

    da.Param(name='bar', default=1, parser=int, description='The number of bars')

    @da.config(top=True)
    def foo() -> None:
        do_bar()

    @da.config()
    def do_bar(bar: int = env()) -> None:
        print(bar)

The usage pattern of the library is as follows:

* First, one must declare all the parameters of all the configured functions by creating
  :py:attr:`dynamake.application.Param` objects.

* All functions that use such parameters must be decorated with
  :py:func:`dynamake.application.config`. They must either be annotated as top level using
  ``top=True``, or be directly invoked from another configured function (and, ultimately, from a
  top-level function).

* Some parameters are already defined and managed for you, such as ``log_level`` to control logging
  and ``random_seed`` to control the random number generation. The latter is only added if you
  specify ``random=True`` when invoking ``config``. Similarly, ``jobs`` controls the number of
  processors used in parallel, and is only added if you specify ``parallel=True`` when invoking
  ``config``.

.. note::

   **The automatic detection of invocations of one configurable function from another is
   simplistic.**

   Basically, if we see inside the function source the name of another function, and this isn't the
   name of a variable being assigned to, then we assume this is a call. This isn't 100% complete;
   for example this will not detect cases where ``foo`` calls a non-configured ``bar`` which then
   calls a configured ``baz``. However it works "well enough" for simple code.

* The configured parameters must use :py:func:`dynamake.application.env` as the parameter's default
  value. This will inject the proper value at each point.

To invoke this function from the command line, run ``dynamain foo`` (or, possibly, ``dynamain
--module my_functions foo``, or ``dynamain --config my_configuration.yaml foo``, etc.). A possible
configuration file for this program would be:

.. code-block:: python

   bar: 2  # The program will print 2 instead of the default 1.

The ``main`` function is self-documenting. Running it with the ``--help`` command line option will
list all the available top-level functions. Running it with ``--help function_name`` will print the
function's documentation, and list all the parameters used by it (or any configurable function it
indirectly invokes).

The :py:attr:`dynamain.application.Prog.logger` provides access to Python's logging facilities,
configured by the ``--log_level`` command line option. That is, just write
``Prog.logger.debug(...)`` etc.

The :py:func:`dynamain.application.parallel` function allows multiple invocations of some function
in parallel. The number of processes used is controlled by the ``--jobs`` command line option. Any
nested ``parallel`` invocation will execute serially, to ensure this limit is respected. To make it
easier to debug code, :py:func:`dynamain.application.serial` has exactly the same interface, but
executes the calls serially.

Finally, you can override the value of some configuration parameters for some code. For example,
the following:

.. code-block:: python

    import dynamake.application as da

    da.Param(name='foo', default=1, parser=int, description='The number of foos')

    @config()
    def print_foo(title: str, foo: int = env()) -> None:
        print(title, foo)

    @config
    def override_foo() -> None:
        print_foo('global:')
        print_foo('explicit:', foo=2)
        with (da.override(foo=3)):
            print_foo('override:')

Will print:

.. code-block:: yaml

    global: 1
    explicit: 2
    override: 3

WHAT NOT (YET)
--------------

Since DynaMake is very new, there are many features that should be implemented, but haven't been
worked on yet:

* Improve the documentation. This README covers the basics but there are additional features that
  are only mentioned in the class and function documentation, and deserves a better description.

* Allow forcing rebuilding (some) targets.

* Dry run. While it is impossible in general to print the full set of dry run actions, if should
  be easy to just print the 1st action(s) that need to be executed. This should provide most of the
  value.

* Allow automated clean actions based on the collected step outputs. If there's nothing
  to be done when building some target(s), then all generated output files (with or without the
  ultimate targets) should be fair game to being removed as part of a clean action. However, due to
  the dry-run problem, we can't automatically clean outputs of actions that depend on actions that
  still need to be executed.

* Allow skipping generating intermediate files if otherwise no actions need to be done. This is very
  hard to do with a dynamic build graph - probably impossible in the general case, but common
  cases might be possible(?)

* Generate a tree (actually a DAG) of step invocations. This can be collected from the persistent
  state files.

* Generate a visualization of the timeline of action executions showing start and end times, and
  possibly also resources consumption. In case of distributed actions, make a distinction between
  submission and completion times and actual start/end times to track the cluster/grid overheads.

* Allow registering additional file formats for the generated configuration files, to allow using
  them for non-DynaMake external actions.

* Allow using checksums instead of timestamps to determine if actions can be skipped, either
  by default or on a per-file basis.
