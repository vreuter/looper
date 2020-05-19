""" Helpers without an obvious logical home. """

from collections import defaultdict, Iterable
from logging import getLogger
import glob
import os
from .const import *
from .exceptions import MisconfigurationException
from peppy.const import *
from peppy import Project as peppyProject
import jinja2
import yaml
import argparse
from ubiquerg import convert_value, merge_dicts

_LOGGER = getLogger(__name__)


def fetch_flag_files(prj=None, results_folder="", flags=FLAGS):
    """
    Find all flag file paths for the given project.

    :param Project | AttributeDict prj: full Project or AttributeDict with
        similar metadata and access/usage pattern
    :param str results_folder: path to results folder, corresponding to the
        1:1 sample:folder notion that a looper Project has. That is, this
        function uses the assumption that if results_folder rather than project
        is provided, the structure of the file tree rooted at results_folder is
        such that any flag files to be found are not directly within rootdir but
        are directly within on of its first layer of subfolders.
    :param Iterable[str] | str flags: Collection of flag names or single flag
        name for which to fetch files
    :return Mapping[str, list[str]]: collection of filepaths associated with
        particular flag for samples within the given project
    :raise TypeError: if neither or both of project and rootdir are given
    """

    if not (prj or results_folder) or (prj and results_folder):
        raise TypeError("Need EITHER project OR rootdir")

    # Just create the filenames once, and pair once with flag name.
    flags = [flags] if isinstance(flags, str) else list(flags)
    flagfile_suffices = ["*{}.flag".format(f) for f in flags]
    flag_suffix_pairs = list(zip(flags, flagfile_suffices))

    # Collect the flag file paths by flag name.
    files_by_flag = defaultdict(list)

    if prj is None:
        for flag, suffix in flag_suffix_pairs:
            flag_expr = os.path.join(results_folder, "*", suffix)
            flags_present = glob.glob(flag_expr)
            files_by_flag[flag] = flags_present
    else:
        # Iterate over samples to collect flag files.
        for s in prj.samples:
            folder = sample_folder(prj, s)
            # Check each candidate flag for existence, collecting if present.
            for flag, suffix in flag_suffix_pairs:
                flag_expr = os.path.join(folder, suffix)
                flags_present = glob.glob(flag_expr)
                files_by_flag[flag].extend(flags_present)

    return files_by_flag


def fetch_sample_flags(prj, sample, pl_name):
    """
    Find any flag files present for a sample associated with a project

    :param looper.Project prj: project of interest
    :param peppy.Sample sample: sample object of interest
    :param str pl_name: name of the pipeline for which flag(s) should be found
    :return Iterable[str]: collection of flag file path(s) associated with the
        given sample for the given project
    """
    sfolder = sample_folder(prj=prj, sample=sample)
    if not os.path.isdir(sfolder):
        _LOGGER.debug("Results folder ({}) doesn't exist for sample {}".
                      format(sfolder, str(sample)))
        return []
    folder_contents = [os.path.join(sfolder, f) for f in os.listdir(sfolder)]
    return [x for x in folder_contents if os.path.splitext(x)[1] == ".flag"
            and os.path.basename(x).startswith(pl_name)]


def grab_project_data(prj):
    """
    From the given Project, grab Sample-independent data.

    There are some aspects of a Project of which it's beneficial for a Sample
    to be aware, particularly for post-hoc analysis. Since Sample objects
    within a Project are mutually independent, though, each doesn't need to
    know about any of the others. A Project manages its, Sample instances,
    so for each Sample knowledge of Project data is limited. This method
    facilitates adoption of that conceptual model.

    :param Project prj: Project from which to grab data
    :return Mapping: Sample-independent data sections from given Project
    """
    if not prj:
        return {}

    try:
        data = prj[CONFIG_KEY]
    except KeyError:
        _LOGGER.debug("Project lacks section '%s', skipping", CONFIG_KEY)
    return data


def partition(items, test):
    """
    Partition items into a pair of disjoint multisets,
    based on the evaluation of each item as input to boolean test function.
    There are a couple of evaluation options here. One builds a mapping
    (assuming each item is hashable) from item to boolean test result, then
    uses that mapping to partition the elements on a second pass.
    The other simply is single-pass, evaluating the function on each item.
    A time-costly function suggests the two-pass, mapping-based approach while
    a large input suggests a single-pass approach to conserve memory. We'll
    assume that the argument is not terribly large and that the function is
    cheap to compute and use a simpler single-pass approach.

    :param Sized[object] items: items to partition
    :param function(object) -> bool test: test to apply to each item to
        perform the partitioning procedure
    :return: list[object], list[object]: partitioned items sequences
    """
    passes, fails = [], []
    _LOGGER.debug("Testing {} items: {}".format(len(items), items))
    for item in items:
        _LOGGER.debug("Testing item {}".format(item))
        group = passes if test(item) else fails
        group.append(item)
    return passes, fails


def sample_folder(prj, sample):
    """
    Get the path to this Project's root folder for the given Sample.

    :param AttributeDict | Project prj: project with which sample is associated
    :param Mapping sample: Sample or sample data for which to get root output
        folder path.
    :return str: this Project's root folder for the given Sample
    """
    return os.path.join(prj.results_folder,
                        sample[SAMPLE_NAME_ATTR])


def get_file_for_project(prj, appendix):
    """
    Create a path to the file for the current project.
    Takes the possibility of amendment being activated at the time

    :param looper.Project prj: project object
    :param str appendix: the appendix of the file to create the path for,
        like 'objs_summary.tsv' for objects summary file
    :return str: path to the file
    """
    fp = os.path.join(prj.output_dir, prj[NAME_KEY])
    if hasattr(prj, AMENDMENTS_KEY) and getattr(prj, AMENDMENTS_KEY):
        fp += '_' + '_'.join(getattr(prj, AMENDMENTS_KEY))
    fp += '_' + appendix
    return fp


def jinja_render_cmd_strictly(cmd_template, namespaces):
    """
    Render a command string in the provided namespaces context.

    Strictly, which means that all the requested attributes must be
    available in the namespaces

    :param str cmd_template: command template do be filled in with the
        variables in the provided namespaces. For example:
        "prog.py --name {project.name} --len {sample.len}"
    :param Mapping[Mapping[str] namespaces: context for command rendering.
        Possible namespaces are: looper, project, sample, pipeline
    :return str: rendered command
    """
    def _finfun(x):
        """
        A callable that can be used to process the result of a variable
        expression before it is output. Joins list elements
        """
        return " ".join(x) if isinstance(x, list) else x

    env = jinja2.Environment(undefined=jinja2.StrictUndefined,
                             variable_start_string="{",
                             variable_end_string="}",
                             finalize=_finfun)
    template = env.from_string(cmd_template)
    try:
        rendered = template.render(**namespaces)
    except jinja2.exceptions.UndefinedError:
        _LOGGER.error("Missing sample, project or pipeline attributes"
                      " required by command template: '{}'"
                      .format(cmd_template))
        raise
    _LOGGER.debug("rendered arg str: {}".format(rendered))
    return rendered


def read_yaml_file(filepath):
    """
    Read a YAML file

    :param str filepath: path to the file to read
    :return dict: read data
    """
    data = None
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f)
    return data


def enrich_args_via_cfg(parser_args, aux_parser):
    """
    Read in a looper dotfile and set arguments.

    Priority order: CLI > dotfile/config > parser default

    :param argparse.Namespace parser_args: parsed args by the original parser
    :return argparse.Namespace: selected argument values
    """
    cfg_args_all = \
        _get_subcommand_args(parser_args.config_file, parser_args.command) \
            if os.path.exists(parser_args.config_file) else None
    result = argparse.Namespace()
    cli_args, _ = aux_parser.parse_known_args()
    for dest in vars(parser_args):
        if dest not in POSITIONAL or not hasattr(result, dest):
            if dest in cli_args:
                x = getattr(cli_args, dest)
                r = convert_value(x) if isinstance(x, str) else x
            elif cfg_args_all is not None and dest in cfg_args_all:
                if isinstance(cfg_args_all[dest], list):
                    r = [convert_value(i) for i in cfg_args_all[dest]]
                else:
                    r = convert_value(cfg_args_all[dest])
            else:
                r = getattr(parser_args, dest)
            setattr(result, dest, r)
    return result


def _get_subcommand_args(cfg_path, subcommand):
    """
    Get the union of values for the subcommand arguments from
    Project.looper.all and Project.looper.<subcommand> sections.

    Additionally, convert the options strings to destinations (replace '-'
    with '_'), which strongly relies on argument parser using default
    destinations.

    :param str cfg_path: path to an existing config file to read
    :param str subcommand: a looper subcommand to select the arguments for
    :return dict: mapping of argument destinations to their values
    """
    args = None
    cfg = peppyProject(cfg_path)
    if CONFIG_KEY in cfg and LOOPER_KEY in cfg[CONFIG_KEY]:
        cfg_args = cfg[CONFIG_KEY][LOOPER_KEY]
        args = cfg_args[ALL_SUBCMD_KEY] if ALL_SUBCMD_KEY in cfg_args else dict()
        args.update(cfg_args[subcommand] if subcommand in cfg_args else dict())
        args = {k.replace("-", "_"): v for k, v in args.items()}
    return args


def init_dotfile(path, cfg_path):
    """
    Initialize looper dotfile

    :param str path: absolute path to the file to initialize
    :param str cfg_path: path to the config file. Absolute or relative to 'path'
    :return bool: whether the file was initialized
    """
    if os.path.exists(path):
        print("Can't initialize, file exists: {}".format(path))
        return False
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(os.path.dirname(path), cfg_path)
    assert os.path.exists(cfg_path), \
        OSError("Provided config path is invalid. You must provide path "
                "that is either absolute or relative to: {}".
                format(os.path.dirname(path)))
    with open(path, 'w') as dotfile:
        yaml.dump({DOTFILE_CFG_PTH_KEY: cfg_path}, dotfile)
    print("Initialized looper dotfile: {}".format(path))
    return True


def read_cfg_from_dotfile():
    """
    Read file path to the config file from the dotfile

    :return str: path to the config file read from the dotfile
    """
    cfg_path = None
    dp = dotfile_path(must_exist=True)
    with open(dp, 'r') as dotfile:
        dp_data = yaml.safe_load(dotfile)
    if DOTFILE_CFG_PTH_KEY in dp_data:
        cfg_path = str(dp_data[DOTFILE_CFG_PTH_KEY])
    return cfg_path


def dotfile_path(directory=os.getcwd(), must_exist=False):
    """
    Get the path to the looper dotfile

    If file existence is forced this function will look for it in
    the directory parents

    :param str directory: directory path to start the search in
    :param bool must_exist: whether the file must exist
    :return str: path to the dotfile
    :raise OSError: if the file does not exist
    """
    cur_dir = directory
    if not must_exist:
        return os.path.join(cur_dir, LOOPER_DOTFILE_NAME)
    while True:
        parent_dir = os.path.dirname(cur_dir)
        if LOOPER_DOTFILE_NAME in os.listdir(cur_dir):
            return os.path.join(cur_dir, LOOPER_DOTFILE_NAME)
        if cur_dir == parent_dir:
            # root, file does not exist
            raise OSError("Looper dotfile ({}) not found in '{}' and all "
                          "its parents".format(LOOPER_DOTFILE_NAME, directory))
        cur_dir = parent_dir
