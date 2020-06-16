from uaperrors import StepError
import sys
import os
from logging import getLogger
from abstract_step import AbstractStep

logger = getLogger('uap_logger')


class SOAPfuse(AbstractStep):
    '''
    SOAPfuse is a tool to discover gene fusions
    in human paired-end RNA-Seq data.

    Paper:
    https://genomebiology.biomedcentral.com/articles/10.1186/gb-2013-14-2-read12

    Manual including required folder structure and typical usage:
    https://sourceforge.net/p/soapfuse/wiki/Home/
   '''

    def __init__(self, pipeline):
        super(SOAPfuse, self).__init__(pipeline)
        self.set_cores(6)

# adding input/output connections
        self.add_connection('in/first_read')
        self.add_connection('in/second_read')
        self.add_connection('out/sf_config')
        self.add_connection('out/sf_sample_list')
        self.add_connection('out/log_stderr')
        self.add_connection('out/log_stdout')
        self.add_connection('out/tar_archive')

# adding required tools
        self.require_tool('soapfuse')
        self.require_tool('cp')
        self.require_tool('mkdir')
        self.require_tool('ln')
        self.require_tool('echo')
        self.require_tool('tar')
        self.require_tool('rm')

# adding options
        self.add_option('es', int, optional=True, default=8,
                        description="The step you want to end at 1-9")

        self.add_option('c', str, optional=False,
                        description=r"""SOAPfuse config;
                        In the config file following variables are overwritten:
                        path to index: DB_db_dir
                        path to soapfuse bin: PG_pg_dir
                        path to soapfuse source: PS_ps_dir
                        suffix for fastq: PA_all_fq_postfix (i.e.: \*fastq.gz)
                        cores: PA_all_process_of_align_software
                        """)

        self.add_option('path_to_index_dir', str, optional=False,
                        description="Sets 'DB_db_dir' in SOAPfuse config")

        self.add_option('path_to_sf_bin_dir', str, optional=False,
                        description="Sets 'PG_pg_dir' in SOAPfuse config")

        self.add_option('path_to_sf_source', str, optional=False,
                        description="Sets 'PS_ps_dir' in SOAPfuse config")

        self.add_option(
            'suffix_for_fq_file',
            str,
            optional=False,
            description="Sets 'PA_all_fq_postfix' in SOAPfuse config")

        self.add_option('read_length', int, optional=False,
                        description="Sets read length for the sample list")

        self.add_option('cores', int, default=6)

    def runs(self, run_ids_connections_files):
        self.set_cores(self.get_option('cores'))

        for run_id in run_ids_connections_files.keys():
            with self.declare_run(run_id) as run:

                # Get list of files for first/second read
                fr_input = run_ids_connections_files[run_id]['in/first_read'][0]
                sr_input = run_ids_connections_files[run_id]['in/second_read'][0]

                # Do we have paired end data and is it exactly one ?
                input_paths = [fr_input]

                if sr_input is None:
                    raise StepError(self, "Not paired end")
                else:
                    input_paths.append(sr_input)

                # create folder structure
                my_input = 'input'
                my_output = 'output'
                my_config = os.path.join(
                    'input', os.path.basename(
                        self.get_option('c')))

                my_sample_dir = os.path.join(my_input, "A", "L")
                read1 = run_id + "_1." + self.get_option('suffix_for_fq_file')
                read2 = run_id + "_2." + self.get_option('suffix_for_fq_file')
                my_sample_read1 = os.path.join(my_sample_dir, read1)
                my_sample_read2 = os.path.join(my_sample_dir, read2)

                # create config file
                res = run.add_output_file(
                    'sf_config',
                    '%s-config.txt' % run_id,
                    input_paths)

                # create sample list
                sample_list = run.add_output_file(
                    'sf_sample_list',
                    '%s-sl.txt' % run_id,
                    input_paths)

                # create logfiles
                log_stderr = run.add_output_file(
                    'log_stderr',
                    '%s-soapfuse-log_stderr.txt' % run_id,
                    input_paths)

                log_stdout = run.add_output_file(
                    'log_stdout',
                    '%s-soapfuse-log_stdout.txt' % run_id,
                    input_paths)

                # init
                with run.new_exec_group() as exec_group:
                    with exec_group.add_pipeline() as pseudo_init:
                        # create folders
                        make_dirs = [self.get_tool('mkdir'), '-p',
                                     my_input,
                                     my_sample_dir,
                                     my_output]

                        pseudo_init.add_command(make_dirs)

                        # copy config
                        cp_cmd = [self.get_tool('cp'),
                                  os.path.abspath(self.get_option('c')),
                                  my_config]

                        pseudo_init.add_command(cp_cmd)

                with run.new_exec_group() as exec_group:

                    # create links to paired-end reads
                    ln_sample = [self.get_tool('ln'), '-s',
                                 fr_input,
                                 my_sample_read1]

                    exec_group.add_command(ln_sample)

                    ln_sample = [self.get_tool('ln'), '-s',
                                 sr_input,
                                 my_sample_read2]

                    exec_group.add_command(ln_sample)

                with run.new_exec_group() as exec_group:
                    # add content  to sample list
                    sample_line = [
                        'A', 'L', run_id, str(
                            self.get_option('read_length'))]
                    sf_list = '\t'.join(sample_line)

                    echo_sf_list = [self.get_tool('echo'),
                                    sf_list]

                    exec_group.add_command(
                        echo_sf_list, stdout_path=sample_list)

                sed_replace = {
                    'DB_db_dir': 'path_to_index_dir',
                    'PG_pg_dir': 'path_to_sf_bin_dir',
                    'PS_ps_dir': 'path_to_sf_source'
                }

                with run.new_exec_group() as exec_group:
                    # replace variables in config
                    with exec_group.add_pipeline() as replace_vars:
                        cat_cmd = ['cat', my_config]
                        replace_vars.add_command(cat_cmd)

                        for tag, option in sed_replace.items():
                            new_path = os.path.abspath(
                                self.get_option('path_to_index_dir'))
                            new_path = new_path.replace("/", "\\/")
                            sed_arg = 's/' + tag + '.*/' + tag + ' = ' + new_path + '/'
                            sed_cmd = ['sed', sed_arg]
                            replace_vars.add_command(sed_cmd)

                        text = ' '.join(['PA_all_fq_postfix', '=',
                                         self.get_option('suffix_for_fq_file')])
                        sed_arg = 's/PA_all_fq_postfix.*/' + text + '/'
                        sed_cmd = ['sed', sed_arg]
                        replace_vars.add_command(sed_cmd)

                        text = ' '.join(['PA_all_process_of_align_software', '=',
                                         str(self.get_option('cores'))])
                        sed_arg = 's/PA_all_process_of_align_software.*/' + text + '/'
                        sed_cmd = ['sed', sed_arg]
                        replace_vars.add_command(sed_cmd, stdout_path=res)

                with run.new_exec_group() as exec_group:
                    # Assemble soapfuse command
                    soapfuse = [
                        self.get_tool('soapfuse'),
                        '-fd', my_input,
                        '-c', res,
                        '-l', sample_list,
                        '-o', my_output,
                        '-es', str(self.get_option('es'))]

                    exec_group.add_command(soapfuse,
                                           stderr_path=log_stderr,
                                           stdout_path=log_stdout)

                with run.new_exec_group() as exec_group:
                    # pack outfolder into tar/zip
                    out_archive = run.add_output_file(
                        'tar_archive',
                        '%s-soapfuse-out.tar.gz' % run_id,
                        input_paths)

                    tar_output = [self.get_tool('tar'),
                                  '-czf', out_archive,
                                  my_output]

                    exec_group.add_command(tar_output)

                with run.new_exec_group() as exec_group:
                    # remove temp dir
                    rm_temp = [self.get_tool('rm'),
                               '-r', my_input, my_output]

                    exec_group.add_command(rm_temp)
