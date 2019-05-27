"""Action for creating packages and registering them with ghc-pkg"""

load("@bazel_skylib//lib:paths.bzl", "paths")
load(":private/path_utils.bzl", "target_unique_name")
load(":private/pkg_id.bzl", "pkg_id")
load(":private/set.bzl", "set")
load(":private/path_utils.bzl", "get_lib_name")

def _get_extra_libraries(dep_info):
    """Get directories and library names for extra library dependencies.

    Args:
      dep_info: HaskellInfo provider of the package.

    Returns:
      (dirs, libs):
      dirs: list: Library search directories for extra library dependencies.
      libs: list: Extra library dependencies.
    """
    cc_libs = dep_info.cc_dependencies.dynamic_linking.libraries_to_link.to_list()

    # The order in which library dependencies are listed is relevant when
    # linking static archives. To maintain the order defined by the input
    # depset we collect the library dependencies in a list, and use a separate
    # set to deduplicate entries.
    seen_libs = set.empty()
    extra_libs = []
    extra_lib_dirs = set.empty()
    for lib in cc_libs:
        lib_name = get_lib_name(lib)

        # This test is a hack. When a CC library has a Haskell library
        # as a dependency, we need to be careful to filter it out,
        # otherwise it will end up polluting extra-libraries, when GHC
        # already uses hs-libraries to locate all Haskell libraries.
        #
        # TODO Get rid of this hack. See
        # https://github.com/tweag/rules_haskell/issues/873.
        if not lib_name.startswith("HS"):
            if not set.is_member(seen_libs, lib_name):
                set.mutable_insert(seen_libs, lib_name)
                extra_libs.append(lib_name)
            set.mutable_insert(extra_lib_dirs, lib.dirname)
    return (set.to_list(extra_lib_dirs), extra_libs)

def package(
        hs,
        dep_info,
        interfaces_dir,
        exposed_modules_file,
        other_modules,
        my_pkg_id):
    """Create GHC package using ghc-pkg.

    Args:
      hs: Haskell context.
      dep_info: HaskellInfo of dependencies.
      interfaces_dir: Directory containing interface files.
      exposed_modules_file: File listing exposed Haskell modules.
      other_modules: Hidden Haskell modules.
      my_pkg_id: This package's pkg_id object.

    Returns:
      (File, File): GHC package conf file, GHC package cache file
    """
    pkg_db_dir = pkg_id.to_string(my_pkg_id)
    conf_file = hs.actions.declare_file(
        paths.join(pkg_db_dir, "{0}.conf".format(pkg_db_dir)),
    )
    cache_file = hs.actions.declare_file("package.cache", sibling = conf_file)

    import_dir = paths.join(
        "${pkgroot}",
        paths.join(pkg_db_dir, "_iface"),
    )

    (extra_lib_dirs, extra_libs) = _get_extra_libraries(dep_info)

    metadata_entries = {
        "name": my_pkg_id.package_name,
        "version": my_pkg_id.version,
        "id": pkg_id.to_string(my_pkg_id),
        "key": pkg_id.to_string(my_pkg_id),
        "exposed": "True",
        "hidden-modules": " ".join(other_modules),
        "import-dirs": import_dir,
        "library-dirs": " ".join(["${pkgroot}"] + extra_lib_dirs),
        "dynamic-library-dirs": " ".join(["${pkgroot}"] + extra_lib_dirs),
        "hs-libraries": pkg_id.library_name(hs, my_pkg_id),
        "extra-libraries": " ".join(extra_libs),
        "depends": ", ".join(dep_info.package_ids),
    }

    # Create a file from which ghc-pkg will create the actual package
    # from. List of exposed modules generated below.
    metadata_file = hs.actions.declare_file(target_unique_name(hs, "metadata"))
    hs.actions.write(
        output = metadata_file,
        content = "\n".join([
            "{0}: {1}".format(k, v)
            for k, v in metadata_entries.items()
            if v
        ]) + "\n",
    )

    # Combine exposed modules and other metadata to form the package
    # configuration file.

    hs.actions.run_shell(
        inputs = [metadata_file, exposed_modules_file],
        outputs = [conf_file],
        command = """
            cat $1 > $3
            echo "exposed-modules: `cat $2`" >> $3
""",
        arguments = [
            metadata_file.path,
            exposed_modules_file.path,
            conf_file.path,
        ],
        use_default_shell_env = True,
    )

    # Make the call to ghc-pkg and use the package configuration file
    cache_file = ghc_pkg_recache(hs, hs.tools.ghc_pkg, conf_file)

    return conf_file, cache_file

def ghc_pkg_recache(ctx, ghc_pkg, conf_file):
    """Generate a package.cache file from the given package configuration

    Args:
      ctx: Rule context or Haskell context.
      ghc_pkg: The ghc-pkg tool.
      conf_file: The package configuration file.

    Returns:
      File, the package.cache file.
    """
    cache_file = ctx.actions.declare_file("package.cache", sibling = conf_file)
    ctx.actions.run(
        executable = ghc_pkg,
        arguments = [
            "recache",
            "--package-db={}".format(conf_file.dirname),
            "-v0",
            "--no-expand-pkgroot",
        ],
        mnemonic = "HaskellRegisterPackage",
        progress_message = "HaskellRegisterPackage {}".format(ctx.label),
        outputs = [cache_file],
        inputs = depset(direct = [conf_file]),
    )
    return cache_file
