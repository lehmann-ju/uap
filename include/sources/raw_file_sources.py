from uaperrors import StepError
import sys
import copy
import csv
import glob
from logging import getLogger
import os
import re
import yaml
from abstract_step import AbstractSourceStep

logger = getLogger('uap_logger')


class RawFileSources(AbstractSourceStep):

    def __init__(self, pipeline):
        super(RawFileSources, self).__init__(pipeline)
        self.add_connection('out/raws')

        self.add_option('pattern', str,
                        description="A file name pattern, for example "
                        "``/home/test/fastq/Sample_*.fastq.gz``.")

        self.add_option(
            'group', str, description="**This is a LEGACY step.** Do NOT use "
            "it, better use the ``raw_file_source`` step. A "
            "regular expression which is applied to found files, "
            "and which is used to determine the sample name from "
            "the file name. For example, "
            r"``(Sample_\d+)_R[12].fastq.gz``, when applied to a "
            "file called ``Sample_1_R1.fastq.gz``, would result in "
            "a sample name of ``Sample_1``. You can specify "
            "multiple capture groups in the regular expression.")

        self.add_option('paired_end', bool,
                        description="Specify whether the samples are paired "
                        "end or not.")

        self.add_option('sample_id_prefix', str, optional=True,
                        description="This optional prefix is prepended to "
                        "every sample name.")

    def declare_runs(self):
        regex = re.compile(self.get_option('group'))
        found_files = dict()

        # find files
        for path in glob.glob(os.path.abspath(self.get_option('pattern'))):
            match = regex.match(os.path.basename(path))
            if match is None:
                raise StepError(self, "Couldn't match regex /%s/ to file %s."
                               % (self.get_option('group'),
                                  os.path.basename(path)))

            sample_id_parts = []
            if self.is_option_set_in_config('sample_id_prefix'):
                sample_id_parts.append(self.get_option('sample_id_prefix'))

            sample_id_parts += list(match.groups())
            sample_id = '_'.join(sample_id_parts)
            if sample_id not in found_files:
                found_files[sample_id] = list()
            found_files[sample_id].append(path)

        # declare a run for every sample
        for run_id, paths in found_files.items():
            with self.declare_run(run_id) as run:
                run.add_public_info(
                    "paired_end", self.get_option("paired_end"))
                for path in paths:
                    run.add_output_file("raws", path, [])
