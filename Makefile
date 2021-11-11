ALL_SOURCE_FILES = $(shell git ls-files)

PY_SOURCE_FILES = $(filter %.py, $(ALL_SOURCE_FILES))

DOCS_SOURCE_FILES = $(filter docs/%, $(ALL_SOURCE_FILES))

NAME = dynamake

MAX_LINE_LENGTH=120  # TODO: Line length is copied into tox.ini

.PHONY: clean clean-test clean-pyc clean-build clean-docs docs help
.DEFAULT_GOAL := help

define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-20s %s" % (target, help.replace('TODO-', 'TODO')))
endef
export PRINT_HELP_PYSCRIPT

help:
	@python -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

clean: clean-make clean-build clean-pyc clean-test clean-docs  ## remove all build, test, coverage and Python artifacts

TODO = todo$()x

pc: $(TODO) style smells docs staged pytest tox  ## verify everything before commit

ci: style smells docs tox  ## verify everything in a CI server

staged:  ## verify everything is staged for git commit
	@if git status . | grep -q 'Changes not staged\|Untracked files'; then git status; false; else true; fi

clean-make:  ## remove make timestamps
	rm -fr .make.*

clean-build:  ## remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc:  ## remove Python file artifacts
	find . -name .mypy_cache -exec rm -fr {} +
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test:  ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

clean-docs:  ## remove documentation artifacts
	rm -fr docs/_build

restyle: do_isort do_black  ## restyle code

do_isort:  ## sort imports with isort
	isort --line-length $(MAX_LINE_LENGTH) --force-single-line-imports $(NAME) tests

do_black:  ## reformat style with black
	black --line-length $(MAX_LINE_LENGTH) $(NAME) tests

style: isort black flake8  # check code style

isort: .make.isort  ## check imports with isort

.make.isort: $(PY_SOURCE_FILES)
	isort --line-length $(MAX_LINE_LENGTH) --force-single-line-imports --check $(NAME) tests
	touch $@

$(TODO): .make.$(TODO)  ## verify there are no leftover TODO-X

.make.$(TODO): $(ALL_SOURCE_FILES)
	@echo 'grep -n -i $(TODO) `git ls-files`'
	@if grep -n -i $(TODO) `git ls-files`; then false; else true; fi
	touch $@

black: .make.black  ## check style with black

.make.black: $(PY_SOURCE_FILES)
	black --line-length $(MAX_LINE_LENGTH) --check $(NAME) tests
	touch $@

flake8: .make.flake8  ## check style with flake8

.make.flake8:
	flake8 --max-line-length $(MAX_LINE_LENGTH) $(NAME) tests
	touch $@

smells: mypy pylint  ## check code smells

pylint: .make.pylint  ## check code with pylint

.make.pylint: $(PY_SOURCE_FILES)
	pylint --max-line-length $(MAX_LINE_LENGTH) $(NAME) tests
	touch $@

mypy: .make.mypy  ## check code with mypy

.make.mypy: $(PY_SOURCE_FILES)
	mypy $(NAME) tests
	touch $@

pytest: .make.pytest  ## run tests quickly with the default Python

.make.pytest: $(PY_SOURCE_FILES)
	pytest -s --cov=$(NAME) --cov-report=html --cov-report=term --no-cov-on-fail
	touch $@

tox: .make.tox  ## run tests on every Python version with tox

.make.tox: $(PY_SOURCE_FILES)
	tox
	touch $@

docs: .make.docs  ## generate Sphinx HTML documentation, including API docs

.make.docs: $(PY_SOURCE_FILES) $(DOCS_SOURCE_FILES)
	rm -f docs/$(NAME).rst
	rm -f docs/modules.rst
	sphinx-apidoc -o docs/ $(NAME)
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
	@echo "Results in docs/_build/html/index.html"
	touch $@

dist: .make.dist  ## builds the release package

.make.dist: $(ALL_SOURCE_FILES)
	make clean
	python setup.py sdist
	ls -l dist
	touch $@

release: .make.dist  ## upload the release package
	twine upload dist/*

install: clean  ## install the package to the active Python's site-packages
	python setup.py install
