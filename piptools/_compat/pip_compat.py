from __future__ import annotations

import optparse
import platform
import re
import typing as _t
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from pip._internal.cache import WheelCache
from pip._internal.exceptions import InstallationError
from pip._internal.index.package_finder import PackageFinder
from pip._internal.metadata import BaseDistribution
from pip._internal.metadata.pkg_resources import Distribution as _PkgResourcesDist
from pip._internal.models.direct_url import DirectUrl
from pip._internal.models.link import Link
from pip._internal.network.session import PipSession
from pip._internal.req import InstallRequirement
from pip._internal.req import parse_requirements as _parse_requirements
from pip._internal.req.constructors import install_req_from_parsed_requirement
from pip._internal.req.req_file import ParsedRequirement
from pip._vendor.pkg_resources import Requirement

# The Distribution interface has changed between pkg_resources and
# importlib.metadata, so this compat layer allows for a consistent access
# pattern. In pip 22.1, importlib.metadata became the default on Python 3.11
# (and later), but is overridable. `select_backend` returns what's being used.
# Secondly, the canonicalize_name function received typing improvements
# in pip 21.2, since mypy runs on an older version, this compat layer ensures correct
# typing regardless of the pip version used. NormalizedName and str are interchangeable.
if _t.TYPE_CHECKING:
    from pip._internal.metadata.importlib import Distribution as _ImportLibDist

    def canonicalize_name(name: str) -> str: ...

else:
    from pip._vendor.packaging.utils import canonicalize_name  # noqa: F401

from .._internal import _pip_api, _relpaths

file_url_schemes_re = re.compile(r"^((git|hg|svn|bzr)\+)?file:")


@dataclass(frozen=True)
class Distribution:
    key: str
    version: str
    requires: Iterable[Requirement]
    direct_url: DirectUrl | None

    @classmethod
    def from_pip_distribution(cls, dist: BaseDistribution) -> Distribution:
        # TODO: Use only the BaseDistribution protocol properties and methods
        # instead of specializing by type.
        if isinstance(dist, _PkgResourcesDist):
            return cls._from_pkg_resources(dist)
        else:
            return cls._from_importlib(dist)

    @classmethod
    def _from_pkg_resources(cls, dist: _PkgResourcesDist) -> Distribution:
        return cls(
            dist._dist.key, dist._dist.version, dist._dist.requires(), dist.direct_url
        )

    @classmethod
    def _from_importlib(cls, dist: _ImportLibDist) -> Distribution:
        """Mimic pkg_resources.Distribution.requires for the case of no
        extras.

        This doesn't fulfill that API's ``extras`` parameter but
        satisfies the needs of pip-tools.
        """
        reqs = (Requirement.parse(req) for req in (dist._dist.requires or ()))
        requires = [
            req
            for req in reqs
            if not req.marker or req.marker.evaluate({"extra": None})
        ]
        return cls(dist._dist.name, dist._dist.version, requires, dist.direct_url)


def parse_requirements(
    filename: str,
    session: PipSession,
    finder: PackageFinder | None = None,
    options: optparse.Values | None = None,
    constraint: bool = False,
    isolated: bool = False,
    from_dir: str | None = None,
) -> Iterator[InstallRequirement]:
    for parsed_req in _parse_requirements(
        filename, session, finder=finder, options=options, constraint=constraint
    ):

        # This context manager helps pip locate relative paths specified
        # with non-URI (non file:) syntax, e.g. '-e ..'
        with _relpaths.working_dir(from_dir):
            try:
                ireq = install_req_from_parsed_requirement(
                    parsed_req, isolated=isolated
                )
            except InstallationError:
                # This can happen when the url is a relpath with a fragment,
                # so we try again with the fragment stripped
                preq_without_fragment = ParsedRequirement(
                    requirement=re.sub(r"#[^#]+$", "", parsed_req.requirement),
                    is_editable=parsed_req.is_editable,
                    comes_from=parsed_req.comes_from,
                    constraint=parsed_req.constraint,
                    options=parsed_req.options,
                    line_source=parsed_req.line_source,
                )
                ireq = install_req_from_parsed_requirement(
                    preq_without_fragment, isolated=isolated
                )

        # At this point the ireq has two problems:
        # - Sometimes the fragment is lost (even without an InstallationError)
        # - It's now absolute (ahead of schedule),
        #   so abs_ireq will not know to apply the _was_relative attribute,
        #   which is needed for the writer to use the relpath.

        # To account for the first:
        if not _relpaths.fragment_string(ireq):
            fragment = Link(parsed_req.requirement)._parsed_url.fragment
            if fragment:
                link_with_fragment = Link(
                    url=f"{ireq.link.url_without_fragment}#{fragment}",
                    comes_from=ireq.link.comes_from,
                    requires_python=ireq.link.requires_python,
                    yanked_reason=ireq.link.yanked_reason,
                    cache_link_parsing=ireq.link.cache_link_parsing,
                )
                ireq = _pip_api.copy_install_requirement(ireq, link=link_with_fragment)

        a_ireq = _relpaths.abs_ireq(ireq, from_dir)

        # To account for the second, we guess if the path was initially relative and
        # set _was_relative ourselves:
        bare_path = file_url_schemes_re.sub(
            "", parsed_req.requirement.split(" @ ", 1)[-1]
        )
        is_win = platform.system() == "Windows"
        if is_win:
            bare_path = bare_path.lstrip("/")
        if (
            a_ireq.link is not None
            and a_ireq.link.scheme.endswith("file")
            and not bare_path.startswith("/")
        ):
            if not (is_win and re.match(r"[a-zA-Z]:", bare_path)):
                a_ireq._was_relative = True

        yield a_ireq


def create_wheel_cache(cache_dir: str, format_control: str | None = None) -> WheelCache:
    kwargs: dict[str, str | None] = {"cache_dir": cache_dir}
    if _pip_api.PIP_VERSION_MAJOR_MINOR <= (23, 0):
        kwargs["format_control"] = format_control
    return WheelCache(**kwargs)


def get_dev_pkgs() -> set[str]:
    if _pip_api.PIP_VERSION_MAJOR_MINOR <= (23, 1):
        from pip._internal.commands.freeze import DEV_PKGS

        return _t.cast(set[str], DEV_PKGS)

    from pip._internal.commands.freeze import _dev_pkgs

    return _t.cast(set[str], _dev_pkgs())
