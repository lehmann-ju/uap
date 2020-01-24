from uaperrors import UAPError
import sys
import os
from logging import getLogger
from abstract_step import AbstractStep

logger=getLogger('uap_logger')

class HtSeqCount(AbstractStep):
    '''
    The htseq-count script counts the number of reads overlapping a feature.
    Input needs to be a file with aligned sequencing reads and a list of genomic
    features. For more information see::

    http://www-huber.embl.de/users/anders/HTSeq/doc/count.html
    '''


    def __init__(self, pipeline):
        super(HtSeqCount, self).__init__(pipeline)

        self.set_cores(2)

        self.add_connection(
            'in/alignments',
            constraints = {'min_files_per_run': 1, 'max_files_per_run': 1}
        )
        self.add_connection(
            'in/features',
            constraints = {'total_files': 1}
        )
        self.add_connection('out/counts')

        self.require_tool('dd')
        self.require_tool('pigz')
        self.require_tool('htseq-count')
        self.require_tool('samtools')

        # Path to external feature file if necessary
        self.add_option('feature-file', str, optional = True)
        # Options for htseq-count
        self.add_option('order', str, choices = ['name', 'pos'],
                        optional = False)
        self.add_option('stranded', str, choices = ['yes', 'no', 'reverse'],
                        optional=False)
        self.add_option('a', int, optional = True)
        self.add_option('type', str, default = 'exon', optional = True)
        self.add_option('idattr', str, default='gene_id', optional = True)
        self.add_option('mode', str, choices = ['union', 'intersection-strict',\
                                                'intersection-nonempty'],
                        default = 'union', optional = True)

        # Options for dd
        self.add_option('dd-blocksize', str, optional = True, default = "256k")

    def runs(self, cc):
        # Compile the list of options
        options = ['order', 'stranded', 'a', 'type', 'idattr', 'mode']

        set_options = [option for option in options if \
                       self.is_option_set_in_config(option)]

        option_list = list()
        for option in set_options:
            if isinstance(self.get_option(option), bool):
                if self.get_option(option):
                    option_list.append('--%s' % option)
            else:
                option_list.append(
                    '--%s=%s' % (option, str(self.get_option(option))))

        if self.is_option_set_in_config('feature-file'):
            option_feature_path = os.path.abspath(self.get_option('feature-file'))
            if not os.path.isfile(option_feature_path):
                raise UAPError('[HTSeqCount]: %s is no file.' %
                        self.get_option('feature-file'))
        else:
            option_feature_path = None
        features_path = cc.look_for_unique('in/features', option_feature_path)
        features_per_run = cc.all_runs_have_connection('in/features')
        if features_per_run is False and features_path is None:
            raise UAPError('No features given for HTSeqCount.')
        allignment_runs = cc.get_runs_with_connections('in/alignments')
        for run_id in allignment_runs:
            # Check input files
            alignments = cc[run_id]['in/alignments']
            input_paths = alignments
            if features_per_run is True:
                features_path = cc[run_id]['in/features'][0]
            if option_feature_path is None:
                input_paths.append(features_path)

            # Is the alignment gzipped?
            root, ext = os.path.splitext(alignments[0])
            is_gzipped = True if ext in ['.gz', '.gzip'] else False
            # Is the alignment in SAM or BAM format?
            if is_gzipped:
                root, ext = os.path.splitext(root)
            is_bam = True if ext in ['.bam'] else False
            is_sam = True if ext in ['.sam'] else False
            if not (bool(is_bam) ^ bool(is_sam)):
                raise UAPError("Alignment file '%s' is neither SAM nor BAM "
                             "format" % alignments[0])
            alignments_path = alignments[0]

            with self.declare_run(run_id) as run:
                with run.new_exec_group() as exec_group:
                    with exec_group.add_pipeline() as pipe:
                        # 1. Read alignment file in 4MB chunks
                        dd_in = [self.get_tool('dd'),
                                 'ibs=%s' % self.get_option('dd-blocksize'),
                                 'if=%s' % alignments_path]
                        pipe.add_command(dd_in)

                        if is_gzipped:
                            # 2. Uncompress file to STDOUT
                            pigz = [self.get_tool('pigz'),
                                    '--decompress',
                                    '--processes', '1',
                                    '--stdout']
                            pipe.add_command(pigz)
                        # 3. Use samtools to generate SAM output
                        if is_bam:
                            samtools = [self.get_tool('samtools'), 'view',
                                        '-h', '-']
                            pipe.add_command(samtools)
                        # 4. Count reads with htseq-count
                        htseq_count = [
                            self.get_tool('htseq-count')
                            #'--format=sam'
                        ]
                        htseq_count.extend(option_list)
                        htseq_count.extend(['-', features_path])
                        pipe.add_command(
                            htseq_count,
                            stdout_path = run.add_output_file(
                                'counts',
                                '%s-htseq_counts.txt' % run_id,
                                input_paths
                            )
                        )
