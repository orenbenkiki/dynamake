NAME = dynamake

MAX_LINE_LENGTH = 120

ALL_SOURCE_FILES = $(shell git ls-files)

PY_SOURCE_FILES = $(filter %.py, $(ALL_SOURCE_FILES))

RST_SOURCE_FILES = $(filter %.rst, $(ALL_SOURCE_FILES))

DOCS_SOURCE_FILES = $(filter docs/%, $(ALL_SOURCE_FILES))

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

clean-make:
	rm -fr .make.*

clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc:
	find . -name .mypy_cache -exec rm -fr {} +
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test:
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

clean-docs:
	rm -fr docs/_build

TODO = todo$()x

pc: is_dev $(TODO) history format smells docs staged pytest tox  ## check everything before commit

ci: history format smells docs tox  ## check everything in a CI server

history:  ## check to-be-done version is described in HISTORY.rst
	@version=`grep 'current_version =' setup.cfg | sed 's/.* //;s/.dev.*//;'`; \
	if grep -q "^$$version" HISTORY.rst; \
	then true; \
	else \
	    echo 'No entry in HISTORY.rst (run `make start_history`).'; \
	    false; \
	fi

staged:  ## check everything is staged for git commit
	@if git status . | grep -q 'Changes not staged\|Untracked files'; \
	then \
	    git status; \
	    echo 'There are unstaged changes (run `git add .`).'; \
	    false; \
	else true; \
	fi

format: backticks linebreaks trailingspaces isort black flake8  ## check code format

trailingspaces: .make.trailingspaces  ## check for trailing spaces

SP_SOURCE_FILES = $(filter-out setup.cfg, $(ALL_SOURCE_FILES))  # TODO: Remove exception when bumpversion is fixed.

.make.trailingspaces: $(SP_SOURCE_FILES)
	@echo "trailingspaces"
	@if grep -Hn '\s$$' $(SP_SOURCE_FILES); \
	then \
	    echo 'Files contain trailing spaces (run `make reformat` or `make stripspaces`).'; \
	    false; \
	else true; \
	fi

linebreaks: .make.linebreaks  ## check line breaks in Python code

.make.linebreaks: $(PY_SOURCE_FILES)
	@echo "linebreaks"
	@if grep -Hn "[^=*][^][/<>\"'a-zA-Z0-9_,:()#}{.?!\\=\`+-]$$" $(PY_SOURCE_FILES) | grep -v -- '--$$'; \
	then \
	    echo 'Files wrap lines after instead of before an operator (fix manually).'; \
	    false; \
	fi

backticks: .make.backticks  ## check usage of backticks in documentation

.make.backticks: $(PY_SOURCE_FILES) $(RST_SOURCE_FILES)
	@echo "backticks"
	@OK=true; \
	for FILE in $(PY_SOURCE_FILES) $(RST_SOURCE_FILES); \
	do \
	    if sed 's/``\([^`]*\)``/\1/g;s/:`\([^`]*\)`/:\1/g;s/`\([^`]*\)`_/\1_/g' "$$FILE" \
	    | grep --label "$$FILE" -n -H '`' \
	    | sed 's//`/g' \
	    | grep '.'; \
	    then OK=false; \
	    fi; \
	done; \
	if $$OK; \
	then true; \
	else \
	    echo 'Documentation contains invalid ` markers (fix manually).'; \
	    false; \
	fi

isort: .make.isort  ## check imports with isort

.make.isort: $(PY_SOURCE_FILES)
	isort --line-length $(MAX_LINE_LENGTH) --force-single-line-imports --check $(NAME) tests
	touch $@

$(TODO): .make.$(TODO)  ## check there are no leftover TODO-X

.make.$(TODO): $(ALL_SOURCE_FILES)
	@echo 'grep -n -i $(TODO) `git ls-files`'
	@if grep -n -i $(TODO) `git ls-files`; \
	then \
	    echo "Files contain $(TODO) markers (fix manually)."; \
	    false; \
	else true; \
	fi
	touch $@

black: .make.black  ## check format with black

.make.black: $(PY_SOURCE_FILES)
	black --line-length $(MAX_LINE_LENGTH) --check $(NAME) tests
	touch $@

flake8: .make.flake8  ## check format with flake8

.make.flake8:
	flake8 --max-line-length $(MAX_LINE_LENGTH) $(NAME) tests
	touch $@

reformat: stripspaces isortify blackify  ## reformat code

stripspaces:  # strip trailing spaces
	@echo stripspaces
	@for FILE in $$(grep -l '\s$$' $$(git ls-files | grep -v setup.cfg)); \
	do sed -i -s 's/\s\s*$$//' $$FILE; \
	done

isortify:  ## sort imports with isort
	isort --line-length $(MAX_LINE_LENGTH) --force-single-line-imports $(NAME) tests

blackify:  ## reformat with black
	black --line-length $(MAX_LINE_LENGTH) $(NAME) tests

smells: mypy pylint  ## check for code smells

pylint: .make.pylint  ## check code with pylint

.make.pylint: $(PY_SOURCE_FILES)
	pylint --max-line-length $(MAX_LINE_LENGTH) $(NAME) tests
	touch $@

mypy: .make.mypy  ## check code with mypy

.make.mypy: $(PY_SOURCE_FILES)
	mypy $(NAME) tests
	touch $@

pytest: .make.pytest  ## run tests on the active Python with pytest

.make.pytest: $(PY_SOURCE_FILES)
	pytest -s --cov=$(NAME) --cov-report=html --cov-report=term --no-cov-on-fail
	touch $@

tox: .make.tox  ## run tests on a clean Python version with tox

.make.tox: $(PY_SOURCE_FILES)
	tox
	touch $@

.PHONY: docs
docs: .make.docs  ## generate HTML documentation

.make.docs: $(PY_SOURCE_FILES) $(DOCS_SOURCE_FILES) $(RST_SOURCE_FILES)
	rm -f docs/$(NAME).rst
	rm -f docs/modules.rst
	sphinx-apidoc -o docs/ $(NAME)
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
	@echo "Results in docs/_build/html/index.html"
	touch $@

committed:  ## check everything is committed in git
	@if [ -z "$$(git status --short)" ]; \
	then true; \
	else \
	    git status; \
	    echo "There are uncommitted changes (run `git commit -m ...`)." \
	    false; \
	fi

install: committed clean  ## install the package into the active Python
	python setup.py install

dist: .make.dist  ## builds the release distribution package

.make.dist: committed $(ALL_SOURCE_FILES)
	make clean
	python setup.py sdist
	ls -l dist
	touch $@

upload: committed is_not_dev .make.dist  ## upload the release distribution package
	else twine upload dist/*

current_version:  # report the current version number
	@grep 'current_version =' setup.cfg

start_patch: committed  ## start working on the next patch version
	@if grep -q 'current_version.*dev' setup.cfg; \
	then \
	    read -p "Skip over releasing the current development version [n]? " answer; \
	    case "$$answer" in \
	        y|yes|Y|Yes|YES) bumpversion patch;; \
	        *) false;; \
	    esac; \
	else bumpversion patch; \
	fi
	@grep 'current_version =' setup.cfg

start_minor: committed  ## start working on the next minor version
	@if grep -q 'current_version.*dev' setup.cfg; \
	then \
	    read -p "Skip over releasing the current development version [n]? " answer; \
	    case "$$answer" in \
	        y|yes|Y|Yes|YES) bumpversion minor;; \
	        *) false ;; \
	    esac; \
	else bumpversion minor; \
	fi
	@grep 'current_version =' setup.cfg

start_major: committed  ## start working on the next major version
	@if grep -q 'current_version.*dev' setup.cfg; \
	then \
	    read -p "Skip over releasing the current development version [n]? " answer; \
	    case "$$answer" in \
	        y|yes|Y|Yes|YES) bumpversion major;; \
	        *) false ;; \
	    esac; \
	else bumpversion major; \
	fi
	@grep 'current_version =' setup.cfg

start_history: is_dev  ## append a history section for the development version
	@version=`grep 'current_version =' setup.cfg | sed 's/.* //;s/.dev.*//;'`; \
	if grep -q "^$$version (WIP)\$$" HISTORY.rst; \
	then true; \
	else \
	    echo >> HISTORY.rst; \
	    echo "$$version (WIP)" >> HISTORY.rst; \
	    echo "$$version" | sed 's/./-/g' >> HISTORY.rst; \
	    echo >> HISTORY.rst; \
	    echo "* ..." >> HISTORY.rst; \
	fi

bump_dev: committed is_dev  ## bump the development version indicator
	bumpversion dev
	@grep 'current_version =' setup.cfg

done_dev: committed done_history is_dev  ## remove the development version indicator
	bumpversion rel
	@grep 'current_version =' setup.cfg

done_history:  ## check to-be-done version is described in HISTORY.rst
	@version=`grep 'current_version =' setup.cfg | sed 's/.* //;s/.dev.*//;'`; \
	if grep -q "^$$version\$$" HISTORY.rst; \
	then true; \
	else \
	    echo "No finalized entry in HISTORY.rst (fix manually)."; \
	    false; \
	fi

is_not_dev:
	@if grep -q 'current_version.*dev' setup.cfg; \
	then \
	    echo "`grep 'current_version =' setup.cfg` is a development version."; \
	    false; \
	fi

is_dev:
	@if grep -q 'current_version.*dev' setup.cfg; \
	then true; \
	else \
	    echo "`grep 'current_version =' setup.cfg` is not a development version."; \
	    false; \
	fi

tags: $(PY_SOURCE_FILES)  ## generate a tags file for vi
	ctags $(PY_SOURCE_FILES)
