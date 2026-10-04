"""Make the claim in .env.example's own header checkable.

The claim
---------
``.env.example`` opens with::

    Every variable the backend reads is declared here, and nothing else is.
    tests/test_config_surface.py enforces both directions by reading
    backend/config.py rather than a list, so a key added there without a line
    here fails the suite.

That is a promise about the world made in the file every operator copies. Before
this test it was a promise about nothing: the file it names did not exist. A
comment asserting an enforcement that is not there is worse than no comment,
because it is load-bearing prose -- it invites the next reader to assume the
sync is machine-checked and to skip the manual comparison it was standing in for.

WHAT IS BEING READ, AND HOW
---------------------------
``backend/config.py`` reads the environment in two distinct shapes, and a parser
that handles only the first will pass while half the configuration is undocumented:

1. Direct literal reads, ``os.getenv("NAME", default)`` -- 11 keys, at
   ``backend/config.py:55`` and neighbours.
2. Reads through the module's own typed wrappers -- ``_env_int``,
   ``_env_float``, ``_env_bool``, ``_env_origin_list`` -- 12 keys. The wrappers
   are what read ``os.getenv(name, default)`` at ``backend/config.py:18``,
   ``backend/config.py:26``, ``backend/config.py:33`` and ``backend/config.py:45``,
   where ``name`` is the wrapper's own *parameter*, not a literal.

Shape 2 is why the wrappers are discovered rather than listed. A regex for
``os.getenv("...")`` returns the 11 direct keys and nothing else, and the two-way
comparison then passes on a file that is missing 12 declarations. This module
therefore finds the wrappers first -- by looking for a function whose body calls a
direct reader with one of its own parameters -- and then resolves each wrapper call
site's literal. A new ``_env_decimal`` wrapper added next year is picked up with
no edit here.

It parses with :mod:`ast` rather than a regex for a second reason:
``backend/main.py:300`` mentions ``os.getenv("CORS_ORIGINS")`` inside a comment
explaining what the old code did. A regex reads that comment as a read site and
attributes a live key to a line of prose. The AST does not see comments at all.

THE UNPARSEABLE CASE, HANDLED LOUDLY
------------------------------------
Only one read in this module is not a literal, and it is a known one: the
``os.getenv(name, default)`` *inside* a wrapper, where ``name`` is the wrapper's
own parameter and the key arrives from the caller. That is not a missing key, it
is the indirection described above, so it is recorded as one and its key is taken
from the call site. Its presence is asserted by
``test_the_wrapper_indirection_is_accounted_for`` rather than assumed.

Every other non-literal read -- a bare variable, an f-string, a concatenation,
anywhere else in the file -- is a genuine unresolvable shape, and this module does
not skip it, because a silent skip is indistinguishable from a passing check.
:func:`_literal_key_argument` raises, naming ``file:line`` and the shape it
found, so the new call site has to be dealt with by a human. There is no
suppression list to add it to, and no key is exempted.

THE TWO DIRECTIONS
------------------
``test_every_read_key_is_declared``
    A key ``config.py`` reads but ``.env.example`` does not declare. The operator
    copies the file, the key stays unset, and the code silently uses its default.
    Nothing errors. That is the drift this test exists to make loud.

``test_no_declared_key_is_unread``
    A key ``.env.example`` declares but ``config.py`` does not read. An operator
    tunes it and watches nothing change, and reasonably concludes the setting is
    broken rather than inert. This is the failure that produced the dead
    ``HOST=0.0.0.0`` and ``PORT=8000`` lines.

A COMMENTED DECLARATION COUNTS AS DECLARED
------------------------------------------
``GOOGLE_API_KEY`` and ``GOOGLE_MODEL`` are declared commented-out, and this
module counts them as declared, in either form, in both directions. The reason is
that the two directions are about *documentation* completeness rather than about
*active configuration*: ``python-dotenv`` never reads a commented line, so a
commented key is a documented key an operator can activate by deleting one
character, which is exactly what the optional-primary-provider block is for.
Uncommenting them to bare ``GOOGLE_API_KEY=`` would satisfy a stricter reading and
would be a worse file -- it would turn an optional setting into a set-but-empty
one. Both sets are reported with the form spelled out, so a key that silently
degrades from active to commented is visible in the failure output rather than
invisible in a passing run.

WHAT IT DOES NOT DO
-------------------
It does not assert the *value* of any default, that a declared key's example value
parses, or that ``config.py`` is the only reader of the environment. The first two
belong to ``tests/test_config.py``; the third is deliberately out of scope, because
``.env.example`` describes the backend's configuration surface, and
``backend/main.py`` is a second file whose reads would need their own decision
about what "declared" means.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "backend" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: A dotenv assignment: ``KEY=value``, tolerating ``export KEY=value``, padding
#: around the ``=``, and a single leading ``#`` for a commented-out declaration.
#: Anchored at the start of the line so that prose is not read as a declaration --
#: the LLM_MAX_TOKENS block discusses a variable without assigning it, and stays
#: out of the comparison because "# 200 is the value ..." opens with a digit and
#: "# Set PERSISTENCE_ENABLED=false ..." opens with a word that is not followed by
#: an ``=``.
#:
#: The leading ``#`` is the one shape here that is genuinely ambiguous: a
#: commented ``# NAME=value`` and a sentence that happens to open with one are the
#: same text. It is resolved in favour of calling it a declaration, because a
#: false positive here is a RED test a human resolves in a minute, while the
#: alternative -- refusing to recognise commented declarations at all -- would let
#: a documented key fall out of the comparison silently. A case restriction was
#: considered and rejected for the same reason: every key in this project is
#: upper-case, so requiring it would hide a lowercase key rather than catch it.
_ASSIGNMENT = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

#: Attribute paths that read the environment directly, as opposed to the
#: ``os.environ`` *module attribute* being looked up. ``os.environ["X"]`` is
#: handled separately, as a subscript, because it is not a call.
_DIRECT_READERS = frozenset(
    {
        "os.getenv",
        "getenv",
        "os.environ.get",
        "os.environ.setdefault",
    }
)

_ENVIRONMENT_OBJECT = "os.environ"


def _dotted_name(node: ast.AST) -> str | None:
    """The attribute path of a node, e.g. ``os.environ.get`` -- or None.

    Walks down through ``ast.Attribute``/``ast.Name`` only. Anything else (a
    subscript, a call, a literal) has no fixed path and returns None, which makes
    an obfuscated read unrecognised as a direct reader rather than misattributed.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _parameter_names(fn: ast.FunctionDef) -> set[str]:
    args = fn.args
    return {
        arg.arg
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    }


def _is_direct_read(node: ast.AST) -> bool:
    """True for ``os.getenv(...)``-shaped reads and ``os.environ["X"]`` subscripts."""
    if isinstance(node, ast.Call):
        return _dotted_name(node.func) in _DIRECT_READERS
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value) == _ENVIRONMENT_OBJECT
    return False


def _reader_wrappers(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """The module's own typed env readers, discovered rather than listed.

    A function qualifies when its body reads the environment through one of *its
    own parameters* -- ``_env_int`` calls ``os.getenv(name, default)`` with the
    parameter ``name``. That indirection is the whole reason a literal search is
    insufficient, so it is also what identifies the wrapper.
    """
    wrappers: dict[str, ast.FunctionDef] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        parameters = _parameter_names(fn)
        for node in ast.walk(fn):
            if not _is_direct_read(node):
                continue
            first = _first_argument(node)
            if isinstance(first, ast.Name) and first.id in parameters:
                wrappers[fn.name] = fn
                break
    return wrappers


def _first_argument(node: ast.AST) -> ast.AST | None:
    """The argument naming the key, for a call or a subscript."""
    if isinstance(node, ast.Subscript):
        return node.slice
    if isinstance(node, ast.Call) and node.args:
        return node.args[0]
    return None


def _literal_key_argument(node: ast.AST, context: str) -> str:
    """The key name at a read site, or an error naming the site.

    This is the documented narrow exception, and it is deliberately loud. The
    unresolvable shapes -- a bare name, an f-string, a concatenation, a variable
    holding the key -- are exactly the ones that would otherwise vanish from the
    comparison, and a vanished read is a false pass.
    """
    argument = _first_argument(node)
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value

    shape = type(argument).__name__ if argument is not None else "no argument"
    raise AssertionError(
        f"{context} reads the environment through an argument that is not a "
        f"string literal ({shape}). This test resolves keys to names, so it "
        "cannot tell you whether that read is declared in .env.example, and "
        "skipping it would be a silent false pass. Pass a literal, or extend "
        "this test to handle the shape on purpose."
    )


def _scopes(tree: ast.Module) -> list[tuple[str | None, list[ast.AST]]]:
    """The module's scopes as ``(enclosing function name, nodes)``.

    Scoped rather than one flat ``ast.walk``, because the whole question of which
    reads are resolvable depends on *where* a read sits. A direct read inside a
    wrapper that takes one of its own parameters is that wrapper's indirection;
    the same read shape sitting anywhere else is a key this test cannot name.
    """
    scopes: list[tuple[str | None, list[ast.AST]]] = []
    module_level = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    scopes.append((None, module_level))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node.name, [node] + list(ast.walk(node))))
    return scopes


def _config_reads() -> tuple[dict[str, set[str]], set[str]]:
    """Every environment key ``backend/config.py`` reads, and where the indirection is.

    Returns ``(reads, indirection)``. ``reads`` maps a key to its ``file:line``
    sites. ``indirection`` holds the ``file:line`` of the wrapper-internal reads
    that resolve through a parameter instead of a literal -- reported, not
    counted, because their key is supplied at the call site.

    Both shapes are resolved: literal direct reads, and calls to the wrappers
    discovered by :func:`_reader_wrappers`.
    """
    source = CONFIG_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CONFIG_PY))
    wrappers = _reader_wrappers(tree)
    reads: dict[str, set[str]] = {}
    indirection: set[str] = set()

    for name, nodes in _scopes(tree):
        wrapper = wrappers.get(name) if name else None
        parameters = _parameter_names(wrapper) if wrapper else set()

        for node in nodes:
            # Skip the wrapper's own function header, which cannot contain a call
            # site, and the defaults evaluated at definition time are not reads.
            if wrapper is not None and node is wrapper:
                continue
            # ast.walk also yields structural nodes with no source position --
            # `arguments` among them -- and a read site has to be citable.
            if not hasattr(node, "lineno"):
                continue

            location = f"{CONFIG_PY.name}:{node.lineno}"
            if _is_direct_read(node):
                argument = _first_argument(node)
                if isinstance(argument, ast.Name) and argument.id in parameters:
                    indirection.add(location)
                    continue
                reads.setdefault(
                    _literal_key_argument(node, location), set()
                ).add(location)
            elif isinstance(node, ast.Call) and _dotted_name(node.func) in wrappers:
                reads.setdefault(
                    _literal_key_argument(node, location), set()
                ).add(location)

    return reads, indirection


def _declared() -> dict[str, set[str]]:
    """Every key ``.env.example`` declares, split by whether the line is active.

    Returned as ``{key: {"active"|"commented", ...}}`` holding the
    ``.env.example:line`` of each form, so a report can say which of the two a
    key currently is rather than only that it exists.
    """
    declared: dict[str, set[str]] = {}
    for number, line in enumerate(ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), 1):
        commented = line.lstrip().startswith("#")
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        form = "commented" if commented else "active"
        declared.setdefault(match.group(1), set()).add(f"{form} {ENV_EXAMPLE.name}:{number}")
    return declared


def _format(sites: set[str]) -> str:
    return ", ".join(sorted(sites))


@pytest.fixture(scope="module")
def parsed() -> tuple[dict[str, set[str]], set[str]]:
    return _config_reads()


@pytest.fixture(scope="module")
def reads() -> dict[str, set[str]]:
    return _config_reads()[0]


@pytest.fixture(scope="module")
def declared() -> dict[str, set[str]]:
    return _declared()


class TestTheParserItselfIsSound:
    """Guards on the parser, because a parser that finds nothing always passes.

    A two-way sync test that resolves zero reads compares two empty sets and
    reports agreement. That is the vacuous pass, and these are the assertions
    that make it unreachable: both shapes must be found, or this module is
    silently broken and the tests below mean nothing.
    """

    def test_it_finds_both_read_shapes(self, reads, parsed):
        _, indirection = parsed
        tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"), filename=str(CONFIG_PY))
        wrappers = _reader_wrappers(tree)
        direct = {
            node
            for _, nodes in _scopes(tree)
            for node in nodes
            if _is_direct_read(node)
            and isinstance(_first_argument(node), ast.Constant)
            and isinstance(_first_argument(node).value, str)
        }

        assert wrappers, (
            f"no wrapper function found in {CONFIG_PY.name}. The typed readers "
            "(_env_int and friends) are discovered by looking for a function "
            "that reads through one of its own parameters; if that detection "
            "stops working, every key read through a wrapper silently vanishes "
            "and both directions below compare an incomplete set"
        )
        assert direct, (
            f"no direct os.getenv literal read found in {CONFIG_PY.name}. The "
            "literal-read shape is the other half of the surface; losing it "
            "would make the two-way comparison pass on wrappers alone"
        )
        assert reads, "the parser resolved no environment reads at all"

    def test_the_wrapper_indirection_is_accounted_for(self, parsed):
        """The parameter reads that stand in for a key are counted, not ignored.

        These are the ``os.getenv(name, ...)`` calls inside the wrappers, whose
        key is supplied by the caller. They are the reason wrappers are resolved
        at their call sites rather than skipped -- and asserting they are still
        there is what proves the wrappers were recognised as readers rather than
        picked up by accident.
        """
        _, indirection = parsed
        assert indirection, (
            "no wrapper-internal parameter read was found, so the wrappers were "
            "not identified as env readers by the indirection this test "
            "documents. If the wrappers are detected by some other means the "
            "docstring above is wrong; if they are not detected at all, the 12 "
            "keys read through them are missing from the comparison"
        )

    def test_it_reads_the_header_claim_it_exists_to_enforce(self):
        """The claim must still be there, or the file is guarding a different promise.

        Cheap, and it is the test that fails if someone deletes this module's
        subject from the header without noticing the header now overstates.
        """
        header = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "tests/test_config_surface.py" in header, (
            f"{ENV_EXAMPLE.name} no longer names this file. If the sync is no "
            "longer enforced here, the header must stop saying it is -- an "
            "enforcement claim without an enforcement is the defect this "
            "module was written to remove"
        )


class TestEnvExampleIsInSyncWithConfig:
    """The two directions, each with the key and the site that proves it."""

    def test_every_read_key_is_declared(self, reads, declared):
        undeclared = {key: sites for key, sites in reads.items() if key not in declared}

        assert not undeclared, (
            "backend/config.py reads environment variables that .env.example "
            "does not declare, so an operator who copies the example and never "
            "reads this file gets the code's default and no hint that a setting "
            "exists:\n"
            + "\n".join(
                f"  {key}  (read at {_format(sites)})" for key, sites in sorted(undeclared.items())
            )
            + "\n\nAdd each key to .env.example, with a comment saying what it "
            "does and which default the code applies when it is unset."
        )

    def test_no_declared_key_is_unread(self, reads, declared):
        unread = {key: sites for key, sites in declared.items() if key not in reads}

        assert not unread, (
            ".env.example declares environment variables that backend/config.py "
            "does not read, so an operator who tunes one watches nothing change "
            "and concludes the setting is broken rather than inert:\n"
            + "\n".join(
                f"  {key}  (declared at {_format(sites)})" for key, sites in sorted(unread.items())
            )
            + "\n\nEither delete the declaration or make config.py read it. Do "
            "not leave a declared key that nothing consumes."
        )
