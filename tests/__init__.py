"""The test suite. A regular package, not an implicit namespace one, on
purpose: spylls 0.1.7's wheel installs its own top-level ``tests`` package
into site-packages, and a regular package anywhere on sys.path beats a
namespace package, which silently broke every ``from tests.corpus ...``
import once spylls (EDT-11) became a dependency."""
