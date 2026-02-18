"""
A place for any extra utility functions used for handling relative paths.
"""

from __future__ import annotations

import os
import platform
import re
import typing as _t
from contextlib import contextmanager

from pip._internal.models.link import Link
from pip._internal.utils.urls import path_to_url, url_to_path

from . import _pip_api

if _t.TYPE_CHECKING:
    from collections.abc import Iterator

    from pip._internal.req import InstallRequirement


@contextmanager
def working_dir(folder: str | None) -> Iterator[None]:
    """Change the current directory within the context, then change it back."""
    if folder is None:
        yield
    else:
        original_dir = os.getcwd()
        try:
            # The os and pathlib modules are incapable of returning an absolute path to the
            # current directory without also resolving symlinks, so this is the realpath.
            # This can be avoided on some systems with, e.g. os.environ["PWD"], but we'll
            # not go there if we don't have to.
            os.chdir(os.path.abspath(folder))
            yield
        finally:
            os.chdir(original_dir)


def fragment_string(ireq: InstallRequirement, omit_egg: bool = False) -> str:
    """
    Return a string like "#egg=pkgname&subdirectory=folder", or "".
    """
    if ireq.link is None or not ireq.link._parsed_url.fragment:
        return ""
    fragment = f"#{ireq.link._parsed_url.fragment.replace(os.path.sep, '/')}"
    if omit_egg:
        fragment = re.sub(r"[#&]egg=[^#&]+", "", fragment).lstrip("#&")
        if fragment:
            fragment = f"#{fragment}"
    fragment = re.sub(r"\[[^\]]+\]$", "", fragment).lstrip("#")
    if fragment:
        fragment = f"#{fragment}"
    # TO CHECK: are hashes already handled? [relpath branch]
    # from main branch:
    # fragments.append(f"{ireq.link.hash_name}={ireq.link.hash}")
    return fragment


def abs_ireq(
    ireq: InstallRequirement, from_dir: str | None = None
) -> InstallRequirement:
    """
    Return the given InstallRequirement if its source isn't a relative path;
    Otherwise, return a new one with the relative path rewritten as absolute.

    In this case, an extra attribute is added: _was_relative,
    which is always True when present at all.
    """
    # We check ireq.link.scheme rather than ireq.link.is_file,
    # to also match <vcs>+file schemes
    if ireq.link is None or not ireq.link.scheme.endswith("file"):
        return ireq

    naive_path = ireq.local_file_path or ireq.link.path
    if platform.system() == "Windows":
        naive_path = naive_path.lstrip("/")

    with working_dir(from_dir):
        url = path_to_url(naive_path).replace("%40", "@")

    if (
        os.path.normpath(naive_path).lower()
        == os.path.normpath(url_to_path(url)).lower()
    ):
        return ireq

    abs_url = f"{url}{fragment_string(ireq)}"
    if "+" in ireq.link.scheme:
        abs_url = f"{ireq.link.scheme.split('+')[0]}+{abs_url}"

    abs_link = Link(
        url=abs_url,
        comes_from=ireq.link.comes_from,
        requires_python=ireq.link.requires_python,
        yanked_reason=ireq.link.yanked_reason,
        cache_link_parsing=ireq.link.cache_link_parsing,
    )

    a_ireq = _pip_api.copy_install_requirement(ireq, link=abs_link)
    a_ireq._was_relative = True

    return a_ireq
