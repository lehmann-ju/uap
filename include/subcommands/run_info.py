#!/usr/bin/env python
# encoding: utf-8

import sys
import logging
import yaml
from textwrap import wrap
import math

import pipeline
import pipeline_info
import command as command_info
from misc import UAPDumper

'''
By default, this script displays information about all runs of the pipeline
configured in 'config.yaml'. But the displayed information can be narrowed
down via command line options.

'''

logger = logging.getLogger("uap_logger")

def ordinal(n):
    """
    Returns an ordinal for a number, e.g., '1st' for 1 and '2nd' for 2.
    """
    return "%d%s" % (n,"tsnrhtdd"[(math.floor(n/10)%10!=1)*(n%10<4)*n%10::4])

def main(args):
    args.no_tool_checks = True
    p = pipeline.Pipeline(arguments=args)
    group_by_status = True

    if args.sources:
        # print all sources (i. e. instances of AbstractSourceStep)
        p.print_source_runs()

    else:
        # print run infos of one or more specific tasks
        shebang = "#!/usr/bin/env bash"
        print(shebang + "\n")
        for task_id in p.get_task_with_list(as_string=True, exclusive=False):
            parts = task_id.split('/')
            if len(parts) != 2:
                raise Exception("Invalid run ID %s." % task_id)
            step_name = parts[0]
            run_id = parts[1]
            run = p.steps[step_name].get_run(run_id)
            report = run.as_dict()
            report_header = "%s/%s -- Report" % (step_name, run_id)
            print("# " + report_header)
            print("# " + "=" * len(report_header) + "\n#")
            dump = yaml.dump(
                report,
                Dumper=UAPDumper,
                default_flow_style=False)
            for line in dump.split('\n'):
                print("# " + line)
            exec_header = "%s/%s -- Commands" % (step_name, run_id)
            print("# " + exec_header)
            print("# " + "=" * len(exec_header) + "\n")
            for eg_count, exec_group in enumerate(run.get_exec_groups(), 1):
                ord = ordinal(eg_count)
                goc_header = "%s Group of Commands" % ord
                pocs = exec_group.get_pipes_and_commands()
                line_end = ""
                if len(pocs) > 1:
                    line_end = " &"
                for count, poc in enumerate(
                        exec_group.get_pipes_and_commands(), 1):
                    # for each pipe or command (poc)
                    # check if it is a pipeline ...

                    cord = ordinal(count)
                    if isinstance(poc, pipeline_info.PipelineInfo):
                        cmd_header = goc_header + " -- %s Pipeline" % cord
                    elif isinstance(poc, command_info.CommandInfo):
                        cmd_header = goc_header + " -- %s Command" % cord
                    print("# " + cmd_header)
                    print("# " + "-" * len(cmd_header) + "\n")
                    cmd = poc.get_command_string() + line_end
                    cmd = wrap(cmd, width=78, break_long_words=False,
                               break_on_hyphens=False,
                               subsequent_indent='  ')
                    print(" \\\n".join(cmd) + '\n')
                if len(pocs) > 1:
                    print("# Waiting for %s Group to Finish" % ord)
                    print("# ------------%s-----------------\n" %
                          ("-" * len(ord)))
                    print("wait" + "\n")
