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

This is a fancy way of saying that the following is supported:

**Dynamic inputs**: The full set of inputs of a build step may depend on a subset of its inputs.

An example of dynamic inputs is compiling a C source file, which actually depends on all the
included header files. This is supported in various ways by most build tools - some of these ways
being more convoluted than others.

**Dynamic outputs**: The set of outputs of a build step may depend on its inputs.

An example of dynamic outputs is running a clustering step on some large data, which may produce any
number of clusters. Each of these clusters needs to go through some further processing. Perhaps only
some of these clusters need to be processed (based on some expensive-to-compute filter).

Dynamic outputs are sufficiently common in scientific computation pipelines that they are a major
source of pain. There are workarounds, for sure. But almost no existing tool has direct support for
them, and of the few tools that do, most do it as an afterthought. Since this issue has wide-ranging
implications on the build tool, this means they typically don't do it well. A notable exception is
`Shake <https://shakebuild.com/>`_, which DynaMake is inspired by.

The problem with dynamic outputs (and, to a lesser extent, dynamic inputs) is that they make other
build tool features really hard. Therefore, retrofitting them into an existing build tool causes
some features to break. In the worst case this leads to silent broken builds.

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
pipeline, and only execute the affected parts of the pipeline, either all the way to the final
results or just to obtain some intermediate results to examine. This use case occurs *a lot* in
scientific computation pipelines.

Most build tools allow some form of configuration. Fewer also allow some (typically inconvenient)
way to provide external per-step configuration. Even fewer also track the configuration so that if
it changes, they know to re-run the affected step.

DynaMake was designed from the start to make it easy to provide such external per-step configuration
and to ensure it is considered as part of the build dependencies.

Python
......

As mentioned above, DynaMake is heavily inspired by `Shake <https://shakebuild.com/>`_. However,
Shake is implemented in `Haskell <https://www.haskell.org/>`_, which isn't as popular as it should
be.

DynaMake was created to address the needs to automating scientific computation pipelines
(specifically in bio-informatics, specifically in single-cell RNA sequencing, not that it matters).
It is much more likely for the typical scientist (or the typical programmer for that matter) to have
at least a passing familiarity with Python. Most haven't even heard of Haskell.

In addition, Python is much more likely to be already installed. It is trivial to just type ``pip
install --user dynamake`` (or ``sudo pip install dynamake`` if you are lucky enough to have ``sudo``
privileges, which most "working stiffs" don't). Installing Shake, well... "not so much".

WHY NOT
-------

DynaMake's unique blend of features comes at some costs:

* It is a new, immature tool. As such, it lacks some features it could/should provide,
  is less efficient than it could be, and you may encounter the occasional bug. Hopefully
  this will improve with time.

* The provided goals, as described above, may be a poor fit for your use case.

  If your build graph and configuration are truly static, consider using `Ninja
  <https://ninja-build.org/>`_ which tries to maximize the benefits of such a static build pipeline.
  It is almost the opposite of DynaMake in this respect.

  If your build graph is only "mostly static" (e.g., just needs a restricted form of dynamic inputs,
  such as included header files), then you have (too) many other options to list here.

WHAT
----

DynaMake is a Python library. Unlike typical Python build tools like `SCons <https://scons.org/>`_,
there's no executable provided as part of the package. Instead, you need to write your build script
in Python, using the library's utilities, and then invoke the provided main function. You can also
directly invoke the build functionality from your own main function.

DynaMake build steps may invoke applications written in any language, which are configured in any
way (command line flags, configuration files, etc.). As a convenience, DynaMake also provides
utilities for writing Python "configurable applications" which make heavy use of DynaMake's
automated configuration control.

Build Scripts
.............

Here is a DynaMake build script which copies the file ``foo`` to the file ``bar``,
if ``bar`` does not exist, or if ``foo`` is newer than ``bar``:

.. code-block:: python

    import dynamake.make as dm

    @dm.action()
    def copy_file(input_path: str, output_path: str) -> dm.Action:
        return dm.Action(input=input_path,
                         output=output_path,
                         run=['cp', input_path, output_path])

    @dm.plan()
    def all() -> None:
        copy_file(input_path='foo', output_path='bar')

    dm.main(default_step=all)

A build script consists of:

* **Actions**: One or more actions, which are Python functions decorated with
  :py:func:`dynamake.make.action`. These must return a :py:class:`.dynamake.make.Action` which
  requires:

  * ``run``: The shell command to execute. This can be a list of strings, or a list of lists of
    strings if multiple commands are needed.

  * ``input``: Either a single string, or a list of strings, detailing the files which will be
    read by the command.

  * ``output``: Either a single string, or a list of strings, detailing the files which will be
    created by the command.

  This is almost exactly a simple ``make``, and is the "ground" level of the flow. When all is said
  and done, the goal of the system is to run the needed actions, just the needed actions, and all
  the needed actions, in correct order, to achieve some goal.

  Similarly to ``make``, the input files already exist and are up-to-date when the function is
  called. Unlike in simple ``make``, the function may use arbitrary code to compute the action. In
  particular, it is allowed to:

  * Query the filesystem to see which files exist.

  * Examine the content of existing files.

  * Contain flow control statements, though this is typically reserved for plan functions.

* **Plans**: Zero or more plans, which are Python functions that invoke "steps" (either actions, or
  sub-plans). These are decorated by :py:func:`dynamake.make.plan`. Plan steps are different from
  action steps in that they may return an arbitrary value, but *not* an
  :py:class:`.dynamake.make.Action`.

  It is the responsibility of the plan step to ensure all the necessary sub-steps are invoked in
  order, such that the inputs for each step exist and are up-to-date before it is called. This
  is in contrast to tools like ``make`` where each rule lists its inputs, and the tool
  searches for the proper rules to invoke to prepare these inputs.

  For dynamic build graphs, "explicit is better than implicit":

  * Explicit plans enable efficient implementation of dynamic build graphs. Locating the proper rule
    for creating an input file is trivial when each rule just lists its inputs (e.g., in ``ninja``),
    but becomes nightmarish if the list of rule outputs is dynamic.

  * For similar reasons, explicit plans make it much easier to understand and debug complex flows.
    Complex, dynamic(-ish) ``make`` files are notoriously difficult to debug, because one has to
    run the rule discovery algorithm in one's head for every input of every rule. When the plans
    are explicit, one just needs to read the list of steps.

* **Main**: Some main function that invokes the build, such as :py:func:`dynamake.make.main`.

An example of a slightly more dynamic build script is:

.. code-block:: python

    import dynamake.make as dm
    from c_source_files import scan_included_files  # Assume this for simplicity.

    @dm.action()
    def compile_file(source_path: str, object_path: str) -> dm.Action:
        return dm.Action(input=scan_included_files(source_path),
                         output=object_path,
                         run=['cc', '-o', object_path, source_path])

    @dm.plan()
    def compile_objects(source_dir: str, object_dir: str) -> List[str]:
       return [compiled.output for compiled
               in dm.foreach(wildcards, compile_file,
                             source_path='{source_dir}/{name}.c',
                             object_path='{object_dir}/{name}.o')]

    @dm.action()
    def link_objects(objects: List[str], executable_path: str) -> dm.Action:
        return dm.Action(input=objects,
                         output=executable_path,
                         run=['ld', '{input}', '-o', '{output}'])

    @dm.plan()
    def build_executable(source_dir: str, object_dir: str, executable_path: str) -> None:
        objects = compile_objects(source_dir, object_dir)
        link_objects(objects, executable_path)

    dm.main(default_step=build_executable)

This demonstrates some additional concepts:

* All DynaMake functions will automatically expand ``{name}`` inside strings.
  The ``name`` can be the name of a function parameter, or the name of a wildcard.
  In actions one can also use ``{input}`` and ``{output}``.

* The: py: func: `dynamake.make.glob` function acts similarly to ``glob.glob``, but will return a list
  of dictionaries, where each one assigns a value to each ``{*name}`` given in the pattern.

  Wildcards lists-of-dictionaries can be used to generate file lists, and/or to invoke multiple
  steps with different parameters.

  DynaMake scripts make heavy use of globbing. The current implementation inefficiently re-executes
  such globs. If this turns out to be a bottleneck, it should be modified to cache the glob results
  to drastically reduce the number of slow file system operations.

  Globbing allows steps to have dynamic outputs in a controlled way. By specifying a glob pattern
  for the outputs of an action, DynaMake can still detect when it needs to be executed, even if the
  set of these files is dynamic: run the action if any of the input files is newer than any of the
  existing files that match the output glob pattern. Either way, the actual list of outputs is
  available in the returned action object, available to be used by additional steps.

Configuration Control
.....................

A major use case of DynaMake is fine-grained control over configuration parameters
for controlling step behavior.

Let's allow configuring the compilation flags in the above example:

.. code-block:: python

    @dm.action()
    def compile_file(source_path: str, object_path: str) -> dm.Action
        return dm.Action(input=scan_included_files(source_path),
                         output: object_path,
                         run=['cc', dm.config_param('flags', ''), '-o', object_path, source_path])

And create a YAML configuration file as follows:

.. code-block:: yaml

   - when:
       step: compile_file
     then:
       flags: [-g, -O2]

   - when:
       step: compile_file
       source_file: src/main.c
     then:
       flags: [-g, -O3]

This configuration file needs to be loaded using :py:func:`dynamake.make.load_config`, which
can be done using a command-line argument if using the provided :py:func:`dynamake.make.main`
function. If we do this, all source files will be compiled with ``-g -O2``, except for
``src/main.c`` which will be compiled with ``-g -O3``.

It is common to manually load a default configuration file before invoking
:py:func:`dynamake.make.main`. In general the last matching rule wins, so any user-specified
configuration using command-line arguments will take precedence over this default configuration.

Generated Configuration Files
.............................

To ensure that changing the configuration of a action will trigger re-computation, if either
:py:func:`dynamake.make.config_file` or :py:func:`dynamake.make.config_param` is invoked in the
action step, then DynaMake wll generate a configuration file for the specific action step invocation
(depending on the step name as well as the values of its function arguments).

For action steps, this file is automatically considered as a dependency. That is, if its content
changes, the action will re-execute. However the configuration file is not added to
:py:attr:`dynamake.make.Action.input`, to make it easier for plan steps to use it as the "real"
inputs file list. Plan steps are always executed so there is no question of dependencies.

The step code can either access the parameter values using :py:func:`dynamake.make.config_param`, or
pass the whole generated file as an action command line argument by invoking
:py:func:`dynamake.make.config_file` (e.g., if the action is implemented using DynaMake's utilities
for writing configurable applications).

If only :py:func:`dynamake.make.config_param` was invoked, then when the step completes, DynaMake
will complain about unused parameters whose ``when`` condition explicitly tested for the ``step``
name. This will detect most typos and "useless" parameters which have no effect on the build.

If :py:func:`dynamake.make.config_file` was invoked, then DynaMake will assume the file is processed
by (some) action, which will take responsibility over detecting unrecognized parameters. To
facilitate this, the generated YAML configuration file contains a sequence of two mappings. The
first contains parameters which need not be used by the action (that is, whose ``when`` clause did
not test the ``step`` name), and the second contains parameters which should be used by the action
(that is, whose ``when`` clause did test the ``step`` name). This allows specifying default
parameters for a large set of steps in a generic rule without complaints about unrecognized
configuration parameters. The generated redundant parameters are somewhat reduced by the fact that a
``when`` clause is automatically false if it examines an argument which does not exist for the step.

The generated configuration file is created in a special directory. By default, this is
``.dynamake``, but this can be overriden using :py:func:`dynamake.make.set_config_dir`, or, if using
the provided :py:func:`dynamake.make.main` function, by setting an environment variable
``DYNAMAKE_DIR`` or providing explicit command line flag.

Configuration Help
..................

Since each step might have its own configuration parameters, it is difficult for the user to know
what can be configured where. DynaMake provides a way to make these steps self-documenting:

.. code-block:: python

    @dm.action(run_help=['foo', '--help'])
    def run_foo(...) -> dm.Action:
        """
        Describe this step.

        The first sentence will be printed in the list of steps. The rest of this documentation
        will be printed on request for help for a specified step. This is a good place to document
        the parameters for steps that use ``config_param``.

        In addition, if ``run_help`` was specified, it can be executed on request for a specified
        step. This should print the list of parameters (especially if the triggered application uses
        the DynaMake application utilities).
        """
        ...
        return { ..., run: ['foo', ...] }

The :py:func:`dynamake.make.main` function provides command-line flags for listing all steps,
printing the documentation of a specified step, or triggering the help command of a specified step.
This "should" list all the available parameters and act as a guide for creating a configuration
file.

Configurable Applications
.........................

Here is a trivial example configurable program:

.. code-block:: python

    import dynamake.application as da


    def main() -> int:
        parser = argparse.ArgumentParser(description='Example')
        da.ConfigArgs.current = da.ConfigArgs({'bar': (1, int, 'The number of bars')})
        da.ConfigArgs.current.add_to_parser(parser)
        args = parser.parse_args()
        da.ConfigArgs.current.parse_args(args)
        print(add(1))  # Bar will be taken from the configuration.


    # Note: the real default of `bar` is 1, not 0!
    @da.config
    def add(foo: int, *, bar: int = 0) -> int:
        return foo + bar

A possible configuration file for this program would be:

.. code-block:: python

   # Parameters that may or may not apply to the program:
   - {}  # None in this case
   # Parameters that "must" apply to the program:
   - bar: 2  # The program will print 3 instead of the default 2.

This file can be passed to the program using the ``--config`` flag, or ``--bar 2`` can be directly
specified instead for the same effect.

The usage pattern of these utilities is as follows:

* First, one must initialize :py:attr:`dynamake.application.ConfigArgs.current` with the list of
  known parameters for the program.

* Typically one then adds all the necessary command line arguments to the program by calling
  :py:func:`dynamake.application.ConfigArgs.add_to_parser`. This registers the ``--config`` flag
  for loading a configuration file and a per-parameter (``--bar`` in the above example) flag
  for explicit overrides.

* After the command line arguments have been parsed, the configuration is finalized using
  :py:func:`dynamake.application.ConfigArgs.parse_args`.

To use the finalized :py:attr:`dynamake.application.ConfigArgs.current` configuration, decorate any
function with :py:func:`dynamake.application.config`. This will use the configuration to provide
default values for each named function argument. Calling the functions with an explicit parameter
value will ignore the configuration's value.

.. note::

   If using `mypy <http://mypy-lang.org/>`_ to type-check the code, then it will complain about
   invocations that do not specify a value for all named arguments. You can work around this by
   providing these arguments with a default value; however, when using the
   :py:func:`dynamake.application.config` decorator, such defaults have **no effect** other than
   shutting ``mypy`` up. This may be confusing for a reader who is not familiar with the
   functionality of the decorator, but is *probably* an acceptable trade-off for being able to
   type-check the code.

WHAT NOT (YET)
--------------

Since DynaMake is very new, there are many features that should be implemented, but haven't been
worked on yet:

Performance Features
....................

* Parallelize execution of actions when possible. Add a ``parallel`` function that invokes
  multiple sub-steps in parallel, allow actions in ``foreach`` to run in parallel, etc.

* Associate resources with actions to limit the parallelism.

* Sumbit actions to a cluster/grid/etc. when possible and sensible. This may require some
  annotation inside the flow (not all actions deserve being distributed). Possibly add a
  ``distributed`` call, and some configuration to allow interfacing with different cluster/grid
  systems. Ensure distribution resources are distinct from parallelism resources.

* Cache the results of glob calls, only invalidate when relevant actions are executed.

Debugging Features
..................

* Generate a serialized log of actions so that the user can manually run a specific one for
  debugging.

* Generate a reason for each executed action.

* Dry run. While it is impossible in general to print the full set of dry run actions, if should
  be easy to just print the 1st action(s) that need to be executed. This should provide most of the
  value.

* Generate a tree (actually a DAG) of step invocations. This would require the steps to be
  sufficiently simple to allow for scanning the Python source code for identifiers containing the
  names of sub-steps.

* Generate a sequence of actions execution order. It isn't possible in general to have an exact
  dependencies DAG due to the dynamic nature of the build graph. However it is possible to sequence
  together the steps invoked by a plan by considering the order in which the invocations appear in
  the source code.

* A deeper analysis of the source code could generate a DAG by detecting when sub-steps need not
  follow each other (e.g., using :py:func:`dynamake.make.foreach` and related functions, in
  different branches of ``if`` ... ``else`` statements, etc.).

* Generate an DAG of the actions for a specific execution. This would be much simpler to generate
  and 100% exact, by tracking the expanded action inputs and outputs.

* Generate a timeline of action executions showing start and end times, and resources consumption.
  In case of distributed actions, make a distinction between submission and completeion times and
  actual start/end times to track the cluster/grid overheads.

* Generate several types of help messages: basic, list all steps, detailed help for a step,
  help of shell action of a step (for parameters).

Application Features
....................

* Application "hub" skeleton for invoking arbitrary (registered) functions with different sets of
  parameters.

Core Features
.............

* Allow skipping missing input or intermediate files if the outputs exist, at least as an option.

* Allow forcing rebuilding some targets.
