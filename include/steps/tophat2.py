from uaperrors import StepError
import sys
import os
from logging import getLogger
from abstract_step import AbstractStep

logger = getLogger('uap_logger')


class TopHat2(AbstractStep):
    '''
    TopHat is a fast splice junction mapper for RNA-Seq reads.
    It aligns RNA-Seq reads to mammalian-sized genomes using the ultra
    high-throughput short read aligner Bowtie, and then analyzes the mapping
    results to identify splice junctions between exons.

    http://tophat.cbcb.umd.edu/

    typical command line::

        tophat [options]* <index_base> <reads1_1[,...,readsN_1]> \
        [reads1_2,...readsN_2]

    Tested on release: TopHat v2.0.13
    '''

    def __init__(self, pipeline):
        super(TopHat2, self).__init__(pipeline)
        self.set_cores(6)

        self.add_connection('in/first_read')
        self.add_connection('in/second_read')
        self.add_connection('out/alignments')
        self.add_connection('out/unmapped')
        self.add_connection('out/insertions')
        self.add_connection('out/deletions')
        self.add_connection('out/junctions')
        self.add_connection('out/misc_logs')
        self.add_connection('out/log_stderr')
        self.add_connection('out/prep_reads')
        self.add_connection('out/align_summary')

        self.require_tool('mkdir')
        self.require_tool('mv')
        self.require_tool('tar')
        self.require_tool('tophat2')

        self.add_option('bowtie1', bool, optional=True,  # default=False,
                        description="Use bowtie1. Default: bowtie2")
        self.add_option('read-mismatches', int, optional=True,  # default=2,
                        desciption="Number of read mismatches")
        self.add_option('read-gap-length', int, optional=True,  # default=2,
                        description="Size of gap length")
        self.add_option('read-edit-dist', int, optional=True,  # default=2,
                        description="Read edit distance")
        self.add_option(
            'read-realign-edit-dist',
            int,
            optional=True,
            description="Read alignment distance. Default: read-edit-dist + 1.")
        self.add_option('min-anchor', int, optional=True,  # default=8,
                        description="Size of minimal anchor.")
        self.add_option('splice-mismatches', int, optional=True,  # default=0,
                        choices=[0, 1, 2],
                        description="Number of splice mismatches")
        self.add_option('min-intron-length', int, optional=True,  # default=50,
                        description="Minimal intron length")
        self.add_option('max-intron-length', int, optional=True,  # default=500000,
                        description="maximal intron length")
        self.add_option('max-multihits', int, optional=True,  # default=20,
                        description="Maximal number of multiple hits")
        self.add_option('supress-hits', bool, optional=True,  # default=False,
                        description="Supress hits")
        self.add_option('transcriptome-max-hits', int, optional=True,  # default=60,
                        description="Max hits in transcriptome")
        self.add_option('prefilter-multihits', bool, optional=True,  # default=False,
                        description="for -G/--GTF option, enable an initial bowtie search "
                        "against the genome")
        self.add_option('max-insertion-length', int, optional=True,  # default=3,
                        description="Max size of insertion")
        self.add_option('max-deletion-length', int, optional=True,  # default=3,
                        description="Max size of deletion")
        self.add_option('solexa-quals', bool, optional=True,  # default=False,
                        description="Qualities are solexa qualities.")
        self.add_option('solexa1.3-quals', bool, optional=True,  # default=False,
                        description="Qualities are solexa1.3 qualities (same as phred64-quals).")
        self.add_option('phred64-quals', bool, optional=True,  # default=False,
                        description="Qualities are phred64 qualities (same as solexa1.3-quals).")
        self.add_option('quals', bool, optional=True,  # default=False,
                        description="Provide/Use (?) qualities.")
        self.add_option('integer-quals', bool, optional=True,  # default=False,
                        description="Provide/Use (?) integer qualities.")
        self.add_option('color', bool, optional=True,  # default=False,
                        description="Solid - color space")
        self.add_option('color-out', bool, optional=True,  # default=False,
                        description="Colored output")
        self.add_option(
            'library_type',
            str,
            optional=False,
            choices=[
                'fr-unstranded',
                'fr-firststrand',
                'fr-secondstrand'],
            description="The default is unstranded (fr-unstranded). "
            "If either fr-firststrand or fr-secondstrand is "
            "specified, every read alignment will have an XS "
            "attribute tag as explained below. Consider supplying "
            "library type options below to select the correct "
            "RNA-seq protocol."
            "(https://ccb.jhu.edu/software/tophat/manual.shtml)")
        self.add_option('index', str, optional=False,
                        description="Path to genome index for tophat2")

    def runs(self, run_ids_connections_files):

        # Check if option values are valid
        if not os.path.exists(self.get_option('index') + '.1.bt2'):
            raise StepError(self, "Could not find index file: %s.*" %
                            self.get_option('index'))

        read_types = {'first_read': '_R1', 'second_read': '_R2'}
        for run_id in run_ids_connections_files.keys():
            with self.declare_run(run_id) as run:
                # Get list of files for first/second read
                fr_input = run_ids_connections_files[run_id]['in/first_read']
                sr_input = run_ids_connections_files[run_id]['in/second_read']

                input_paths = [y for x in [fr_input, sr_input]
                               for y in x if y is not None]

                # Do we have paired end data?
                is_paired_end = True
                if sr_input == [None]:
                    is_paired_end = False

                # Tophat is run in this exec group
                with run.new_exec_group() as exec_group:
                    # 2. Create temporary directory for tophat2 output
                    temp_out_dir = run.add_temporary_directory(
                        "tophat-%s" % run_id)
                    mkdir = [self.get_tool('mkdir'), temp_out_dir]
                    exec_group.add_command(mkdir)

                    # 3. Map reads using tophat2
                    tophat2 = [
                        self.get_tool('tophat2'),
                        '--library-type', self.get_option('library_type'),
                        '--output-dir', temp_out_dir,
                        '-p', str(self.get_cores()),
                        os.path.abspath(self.get_option('index')),
                        ','.join(fr_input)
                    ]

                    if is_paired_end:
                        tophat2.append(','.join(sr_input))

                    exec_group.add_command(
                        tophat2,
                        stderr_path=run.add_output_file(
                            'log_stderr',
                            '%s-tophat2-log_stderr.txt' % run_id, input_paths)
                    )

                    # Move files created by tophat2 to their final location
                    tophat2_generic_files = [
                        'accepted_hits.bam', 'unmapped.bam', 'insertions.bed',
                        'deletions.bed', 'junctions.bed', 'prep_reads.info',
                        'align_summary.txt'
                    ]

                    # Define output files
                    tophat2_files = {
                        'accepted_hits.bam': run.add_output_file(
                            'alignments',
                            '%s-tophat2-accepted.bam' % run_id,
                            input_paths),
                        'unmapped.bam': run.add_output_file(
                            'unmapped',
                            '%s-tophat2-unmapped.bam' % run_id,
                            input_paths),
                        'insertions.bed': run.add_output_file(
                            'insertions',
                            '%s-tophat2-insertions.bed' % run_id,
                            input_paths),
                        'deletions.bed': run.add_output_file(
                            'deletions',
                            '%s-tophat2-deletions.bed' % run_id,
                            input_paths),
                        'junctions.bed': run.add_output_file(
                            'junctions',
                            '%s-tophat2-junctions.bed' % run_id,
                            input_paths),
                        'prep_reads.info': run.add_output_file(
                            'prep_reads',
                            '%s-tophat2-prep_reads.info' % run_id,
                            input_paths),
                        'align_summary.txt': run.add_output_file(
                            'align_summary',
                            '%s-tophat2-align_summary.txt' % run_id,
                            input_paths)
                    }

                # Move files from tophat2 temporary output directory to final
                # destination
                with run.new_exec_group() as clean_up_exec_group:
                    for generic_file, final_path in tophat2_files.items():
                        mv = [self.get_tool('mv'),
                              os.path.join(temp_out_dir, generic_file),
                              final_path
                              ]
                        clean_up_exec_group.add_command(mv)

                    tar_logs = [self.get_tool('tar'),
                                '--remove-files',
                                '-C', temp_out_dir,
                                '-czf',
                                run.add_output_file(
                                    'misc_logs',
                                    '%s-tophat2-misc_logs.tar.gz' % run_id,
                                    input_paths),
                                'logs'
                                ]
                    clean_up_exec_group.add_command(tar_logs)
