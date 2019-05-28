#!/usr/bin/env python3
#
# Convert a package database to a .bzl file loadable from a BUILD
# description.
#
# Arguments:;
#     REPO_NAME: the name of the external repository in which the database is stored.
#     TOP_DIR: The path to GHC's $topdir, i.e. the lib/ folder of any installation.

from __future__ import unicode_literals

import glob
import os
import sys
import textwrap
import types

if len(sys.argv) == 3:
    repo_dir = "external/" + sys.argv[1]
    topdir = sys.argv[2]
else:
    sys.exit("Usage: pkgdb_to_bzl.py <REPO_NAME> <TOPDIR>")

def unfold_fields(content):
    """Unfold fields that were split over multiple lines.

    Returns:
        A list of field name/value pairs.
    """
    fields = []
    for line in content.splitlines():
        if line.startswith(" "):
            fields[-1] += line
        elif line:
            fields.append(line)
    return fields

def path_to_label(path, pkgroot):
    """Substitute one pkgroot for another relative one to obtain a label."""
    topdir_relative_path = path.replace(pkgroot, "$topdir")
    if topdir_relative_path.find("$topdir") != -1:
        return topdir_relative_path.replace("$topdir", topdir).replace('\\', '/')

def hs_library_pattern(name, mode = "static", config = ""):
    """Convert hs-libraries entry to glob patterns.

    Args:
        name: The library name. E.g. HSrts or Cffi.
        mode: The linking mode. Either "static" or "dynamic".
        suffix: The RTS configuration suffix. See https://gitlab.haskell.org/ghc/ghc/wikis/commentary/rts/config#rts-configurations

    Returns:
        List of globbing patterns for the library file.

    """
    if name == "Cffi" and mode == "dynamic":
        libname = "ffi"
    else:
        libname = name + config
        if mode == "dynamic":
            libname += "-ghc*"
    if mode == "dynamic":
        exts = ["so", "dylib", "dll"]
    else:
        exts = ["a"]
    return [
        "lib{}.{}".format(libname, ext)
        for ext in exts
    ]

output = []

# Accumulate package id to package name mappings.
pkg_id_map = []
for conf in glob.glob(os.path.join(topdir, "package.conf.d", "*.conf")):
    with open(conf, 'r') as f:
        content = f.read()
    fields = unfold_fields(content)
    pkg = types.SimpleNamespace(
        exposed_modules = [],
        hidden_modules = [],
        import_dirs = [],
        include_dirs = [],
        library_dirs = [],
        dynamic_library_dirs = [],
        depends = [],
        hs_libraries = [],
        ld_options = [],
        extra_libraries = [],
    )
    for field in fields:
        key, value = field.split(":", 1)
        value = value.strip()
        if key == "name":
            pkg.name = value
        elif key == "version":
            pkg.version = value
        elif key == "id":
            pkg.id = value
        elif key == "key":
            pkg.key = value
        elif key == "exposed-modules":
            pkg.exposed_modules += value.split()
        elif key == "hidden-modules":
            pkg.hidden_modules += value.split()
        elif key == "import-dirs":
            pkg.import_dirs += value.split()
        elif key == "include-dirs":
            pkg.include_dirs += value.split()
        elif key == "library-dirs":
            pkg.library_dirs += value.split()
        elif key == "dynamic-library-dirs":
            pkg.dynamic_library_dirs += value.split()
        elif key == "hs-libraries":
            pkg.hs_libraries += value.split()
        elif key == "depends":
            pkg.depends += value.split()
        elif key == "ld-options":
            pkg.ld_options += [opt.strip('"') for opt in value.split()]
        elif key == "extra-libraries":
            pkg.extra_libraries += value.split()

    # pkgroot is not part of .conf files. It's a computed value. It is
    # defined to be the directory enclosing the package database
    # directory.
    pkg.pkgroot = os.path.dirname(os.path.dirname(os.path.realpath(conf)))

    pkg_id_map.append((pkg.name, pkg.id))
    output += [
        # We substitute globs instead of actual paths because the
        # libraries could be anywhere in the various library dirs, in
        # static or shared form, or neither.
        textwrap.dedent("""\
            haskell_import(
                name = "{name}",
                id = "{id}",
                key = "{key}",
                deps = {deps},
                exposed_modules = {exposed_modules},
                hidden_modules = {hidden_modules},
                import_dirs = {import_dirs},
                interface_files = {interface_files},
                hdrs = {hdrs},
                includes = {includes},
                linkopts = {linkopts},
                shared_libraries = {shared_libraries},
                static_libraries = {static_libraries},
                static_profiling_libraries = {static_profiling_libraries},
                version = "{version}",
                visibility = ["//visibility:public"],
                ghc_pkg = ":bin/ghc-pkg",
            )
            """.format(
                name = pkg.name,
                id = pkg.id,
                key = pkg.key,
                version = pkg.version,
                exposed_modules = pkg.exposed_modules,
                hidden_modules = pkg.hidden_modules,
                import_dirs = "{}".format([
                    path_to_label(import_dir, pkg.pkgroot)
                    for import_dir in pkg.import_dirs
                ]),
                interface_files = "glob({})".format([
                    path_to_label("{}/**/*.{}".format(import_dir, ext), pkg.pkgroot)
                    for import_dir in pkg.import_dirs
                    for ext in ["hi", "p_hi", "dyn_hi"]
                ]),
                hdrs = "glob({})".format([
                    path_to_label("{}/**/*.h".format(include_dir), pkg.pkgroot)
                    for include_dir in pkg.include_dirs
                    if path_to_label(include_dir, pkg.pkgroot)
                ]),
                includes = [
                    "/".join([repo_dir, path_to_label(include_dir, pkg.pkgroot)])
                    for include_dir in pkg.include_dirs
                    if path_to_label(include_dir, pkg.pkgroot)
                ],
                static_libraries = "glob({})".format([
                    path_to_label("{}/{}".format(library_dir, pattern), pkg.pkgroot)
                    for hs_library in pkg.hs_libraries
                    for pattern in hs_library_pattern(hs_library, mode = "static", config = "")
                    for library_dir in pkg.library_dirs
                    if path_to_label(library_dir, pkg.pkgroot)
                ]),
                static_profiling_libraries = "glob({})".format([
                    path_to_label("{}/{}".format(library_dir, pattern), pkg.pkgroot)
                    for hs_library in pkg.hs_libraries
                    for pattern in hs_library_pattern(hs_library, mode = "static", config = "_p")
                    for library_dir in pkg.library_dirs
                    if path_to_label(library_dir, pkg.pkgroot)
                ]),
                shared_libraries = "glob({})".format([
                    path_to_label("{}/{}".format(dynamic_library_dir, pattern), pkg.pkgroot)
                    for hs_library in pkg.hs_libraries
                    for pattern in hs_library_pattern(hs_library, mode = "dynamic", config = "")
                    for dynamic_library_dir in pkg.dynamic_library_dirs + pkg.library_dirs
                    if path_to_label(dynamic_library_dir, pkg.pkgroot)
                ]),
                deps = pkg.depends,
                linkopts = pkg.ld_options + [
                    "-L{}".format(library_dir)
                    for library_dir in pkg.dynamic_library_dirs + pkg.library_dirs
                    if not path_to_label(library_dir, pkg.pkgroot)
                ] + [
                    "-l{}".format(extra_library)
                    for extra_library in pkg.extra_libraries
                ],
            )
        )
    ]

for pkg_name, pkg_id in pkg_id_map:
    if pkg_id != pkg_name:
        output += ["""alias(name = '{}', actual = '{}')""".format(pkg_id, pkg_name)]

output += [
    textwrap.dedent("""
      toolchain_libraries = {pkgs}
      """.format(pkgs = [pkg_name for pkg_name, _ in pkg_id_map])
    )
]

print("\n".join(output))
