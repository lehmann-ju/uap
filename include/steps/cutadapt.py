import sys
from abstract_step import *
import pipeline
import subprocess
import yaml
import unix_pipeline

class Cutadapt(AbstractStep):
    def __init__(self, pipeline):
        super(Cutadapt, self).__init__(pipeline)
        self.set_cores(6)

    def setup_runs(self, complete_input_run_info):
        output_run_info = {}
        for input_run_id, input_run_info in complete_input_run_info.items():
            for in_path in sorted(input_run_info['output_files']['reads'].keys()):
                # determine which read this is (R1 or R2)
                which = None
                if '_R1_' in in_path:
                    which = 'R1'
                elif '_R2_' in in_path:
                    which = 'R2'
                else:
                    raise StandardError("Expected input files with _R1_ or _R2_.")

                output_run_id = input_run_id + '-' + which

                if not output_run_id in output_run_info:
                    output_run_info[output_run_id] = {
                        'output_files': {},
                        'info': {
                            'read': which
                        }
                    }

                for t in [('reads', input_run_id + '-cutadapt-' + which + '.fastq.gz'),
                        ('log', input_run_id + '-cutadapt-' + which + '-log.txt')]:
                    pathkey = t[0]
                    path = t[1]
                    if not pathkey in output_run_info[output_run_id]['output_files']:
                        output_run_info[output_run_id]['output_files'][pathkey] = {}
                    if not path in output_run_info[output_run_id]['output_files'][pathkey]:
                        output_run_info[output_run_id]['output_files'][pathkey][path] = []
                    output_run_info[output_run_id]['output_files'][pathkey][path].append(in_path)

        return output_run_info

    def execute(self, run_id, run_info):
        # basic sanity check
        if len(run_info['output_files']['reads']) != 1:
            raise StandardError("Expected a single output file.")

        # find adapter
        adapter = ''
        if run_info['info']['read'] == 'R1':
            adapter = self.options['adapter-R1']
        elif run_info['info']['read'] == 'R2':
            adapter = self.options['adapter-R2']
        else:
            raise StandardError("Expected R1 or R2.")

        # insert correct index if necessary
        if '((INDEX))' in adapter:
            # TODO: this is weird, we need something more general
            sample_info = self.pipeline.all_samples[run_id[0:-3]]
            index = sample_info['lanes'].values()[0]['Index']
            adapter = adapter.replace('((INDEX))', index)

        # set up processes
        pigz1 = [self.tool('pigz'), '-d', '-c']
        pigz1.extend(*sorted(run_info['output_files']['reads'].values()))

        cutadapt = [self.tool('cutadapt'), '-a', adapter, '-']

        pigz2 = [self.tool('pigz'), '--blocksize', '4096', '--processes', '3', '-c']

        # create the pipeline and run it
        up = unix_pipeline.UnixPipeline()
        up.append(pigz1)
        up.append(cutadapt, stderr = open(run_info['output_files']['log'].keys()[0], 'w'))
        up.append(pigz2, stdout = open(run_info['output_files']['reads'].keys()[0], 'w'))

        up.run()
