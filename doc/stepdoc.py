#!../python_env/bin/python

import yaml
import string
import os
import logging
import glob
import sys
sys.path.append('../include')
sys.path.append('../include/steps')
sys.path.append('../include/sources')
from pipeline import coreutils
import abstract_step


def doc_module(module_name, fout, uap_tools):
    step_class = abstract_step.AbstractStep.get_step_class_for_key(module_name)
    step = step_class(None)
    fout.write(".. index:: %s\n" % module_name)
    fout.write("\n")
    fout.write(module_name + "\n")
    fout.write('=' * len(module_name) + "\n\n")
    fout.write("\n")
    if step.__doc__:
        doc = step.__doc__.split("\n")
        for line in doc:
            fout.write(line.rstrip() + "\n")

    # print connections
    in_con = step.get_in_connections()
    out_con = step.get_out_connections()
    if in_con:
        fout.write("**Input Connection**\n")
        for con in sorted(in_con):
            fout.write("  - **%s**" % con)
            if con in step._optional_connections:
                fout.write(" (optional)")
            if con in step._connection_formats.keys():
                format = step._connection_formats[con]
                fout.write(" Format: **%s**" % format)
            if con in step._connection_descriptions.keys():
                fout.write(' - %s' % step._connection_descriptions[con])
            fout.write("\n")
        fout.write("\n")
    if out_con:
        fout.write("**Output Connection**\n")
        for con in sorted(out_con):
            fout.write("  - **%s**" % con)
            if con in step._optional_connections:
                fout.write(" (optional)")
            if con in step._connection_formats.keys():
                format = step._connection_formats[con]
                fout.write(" Format: **%s**" % format)
            if con in step._connection_descriptions.keys():
                fout.write(' - %s' % step._connection_descriptions[con])
            fout.write("\n")
        fout.write("\n")
    fout.write("\n")
    fout.write(".. graphviz::\n")
    fout.write("\n")
    fout.write("   digraph foo {\n")
    fout.write("      rankdir = LR;\n")
    fout.write("      splines = true;\n")
    fout.write(
        "      graph [fontname = Helvetica, fontsize = 12, size = \"14, 11\", nodesep = 0.2, ranksep = 0.3];\n")
    fout.write(
        "      node [fontname = Helvetica, fontsize = 12, shape = rect];\n")
    fout.write("      edge [fontname = Helvetica, fontsize = 12];\n")
    fout.write(
        "      %s [style=filled, fillcolor=\"#fce94f\"];\n" %
        module_name)
    for index, c in enumerate(sorted(step.get_connections())):
        if c in step._optional_connections:
            fill = ', style=filled, fillcolor="#a7a7a7"'
        else:
            fill = ''
        c = c.split('/')
        if c[0] == 'out':
            fout.write(
                "      out_%d [label=\"%s\"%s];\n" %
                (index, c[1], fill))
            fout.write("      %s -> out_%d;\n" % (module_name, index))
        else:
            fout.write("      in_%d [label=\"%s\"%s];\n" % (index, c[1], fill))
            fout.write("      in_%d -> %s;\n" % (index, module_name))
    fout.write("   }\n")
    fout.write("\n")

    # print options
    if len(step._defined_options) > 0:
        fout.write("**Options:**\n")
        for key in sorted(step._defined_options.keys()):
            option = step._defined_options[key]
            fout.write("  - **%s** (%s, %s)" % (
                key,
                '/'.join([_.__name__ for _ in option['types']]),
                'optional' if option['optional'] else 'required'
            ))
            if option['description']:
                fout.write(" -- %s" % option['description'])
                fout.write("\n")
            if option['default']:
                fout.write("    - default value: %s\n" % option['default'])
            if option['choices']:
                fout.write("    - possible values: %s\n" %
                           ", ".join(["'%s'" % x for x in option['choices']]))
                fout.write("\n")
            fout.write("\n")
        fout.write("\n")

    # print tools
    def tooltag(tool):
        return ' (coreutils)' if tool in coreutils else ''
    tools = [t+tooltag(t) for t in step._tools.keys() if t not in uap_tools]
    if tools:
        fout.write("**Required tools:** %s\n" % ', '.join(sorted(tools)))
        fout.write("\n")

    if abstract_step.AbstractSourceStep in step.__class__.__bases__:
        # this is a source step which does not create any tasks
        fout.write(
            "This step provides input files which already exists and therefore creates no tasks in the pipeline.\n")
        fout.write("\n")
    else:
        # this is a step which creates tasks
        fout.write("**CPU Cores:** %s\n" % step._cores)
        fout.write("\n")

    '''
    print("Cores: %d" % step._cores)
    print("Connections: %s" % step._connections)
    print("Tools: %s" % step._tools)
    print("Options: %s" % sorted(step._defined_options.keys()))
    print(step.__doc__)
    '''


def main():
    abs_path = os.path.dirname(os.path.realpath(__file__))
    uap_tools = glob.glob(os.path.join(abs_path, '../tools/*.py'))
    uap_tools = [os.path.basename(t).replace('.py', '') for t in uap_tools]
    with open(os.path.join(abs_path, 'source/steps.rst'), 'w') as fout:
        fout.write("###############\n")
        fout.write("Available steps\n")
        fout.write("###############\n")
        fout.write("\n")
        fout.write("************\n")
        fout.write("Source steps\n")
        fout.write("************\n\n")
        modules = glob.glob(os.path.join(abs_path, '../include/sources/*.py'))
        for m in sorted(modules):
            module_name = os.path.basename(m).replace('.py', '')
            if '__' not in module_name:
                doc_module(module_name, fout, uap_tools)
        fout.write("****************\n")
        fout.write("Processing steps\n")
        fout.write("****************\n\n")
        modules = glob.glob(os.path.join(abs_path, '../include/steps/*.py'))
        for m in sorted(modules):
            module_name = os.path.basename(m).replace('.py', '')
            if module_name == 'io_step':
                continue
            if '__' not in module_name:
                doc_module(module_name, fout, uap_tools)


if __name__ == '__main__':
    logging.basicConfig()
    logger = logging.getLogger("uap_logger")
    info_formatter = logging.Formatter(
        fmt='[uap][%(levelname)s]: %(message)s '
    )
    # create console handler
    ch = logging.StreamHandler()
    # set handler logging level
    ch.setLevel(logging.NOTSET)
    # add formatter to ch
    ch.setFormatter(info_formatter)
    # add ch to logger
    logger.addHandler(ch)
    # Instantiate logger
    # set logger logging level
    logger.setLevel(logging.ERROR)

    main()
